import json

import pytest
import requests
from fastapi.testclient import TestClient

from test_web import build_test_app
from v2t.services import bilibili_catalog as catalog


BV = "BV19r4y1w7Nx"
OTHER = "BV1o4p568Etd"
VIDEO = f"https://www.bilibili.com/video/{BV}/"
FIRST = {"page": 1, "part": "第一讲", "duration": 109, "cid": 123}
SECOND = {"page": 2, "part": "第二讲", "duration": 1358, "cid": 124}


@pytest.fixture(autouse=True)
def fresh_catalog_cache():
    catalog._catalog_cache.clear()
    yield
    catalog._catalog_cache.clear()


def mock_view(monkeypatch, data, *, code=0, status=200, redirect=None, pagelist=None, view_timeout=False):
    calls = []

    def get(session, url, **opts):
        calls.append((url, opts.get("params")))
        assert opts["allow_redirects"] is False and opts["timeout"] and opts["stream"]
        response = requests.Response()
        response.status_code = status
        payload = {"code": code, "data": data}
        if url == "https://b23.tv/abc" and redirect:
            response.status_code = 302
            response.headers["Location"] = redirect
        elif url == catalog._PAGELIST:
            response.status_code = 200
            payload = {"code": 0, "data": pagelist}
        else:
            assert url == catalog._VIEW, f"unexpected network request: {url}"
            if view_timeout:
                raise requests.Timeout
        response._content = json.dumps(payload).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.Session, "get", get)
    return calls


def test_readonly_preview_returns_canonical_selected_parts_and_no_tasks(tmp_path, monkeypatch):
    calls = mock_view(monkeypatch, {"bvid": BV, "title": "课程", "pages": [FIRST, SECOND]})
    app, service, _, _ = build_test_app(tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/bilibili/catalog", json={"source": f"分享：{VIDEO}?p=2&vd_source=tracking"})
        assert r.status_code == 200
        assert r.json() == {"kind": "parts", "title": "课程", "items": [
            {"source": VIDEO + "?p=1", "title": "第一讲", "duration": 109, "selected": False},
            {"source": VIDEO + "?p=2", "title": "第二讲", "duration": 1358, "selected": True},
        ]}
        assert client.get("/api/tasks").json()["items"] == []
    assert calls == [(catalog._VIEW, {"bvid": BV})]


def test_cross_bv_season_preserves_section_order_and_multipart_episode_identity(monkeypatch):
    other_pages = [FIRST, SECOND]
    other = {"bvid": OTHER, "title": "另一门课", "pages": other_pages}
    this = {"bvid": BV, "title": "本节", "page": {"page": 1, "duration": 64}}
    invalid = {"bvid": "../evil.com", "title": "不可用"}
    season = {"title": "课程合集", "sections": [
        {"episodes": [other, invalid]}, {"episodes": [this, other]},
    ]}
    mock_view(monkeypatch, {"bvid": BV, "title": "本节", "pages": [FIRST], "ugc_season": season})
    parsed = catalog.preview_bilibili_catalog(BV)
    assert (parsed.kind, parsed.title) == ("season", "课程合集")
    assert [item.source for item in parsed.items] == [
        f"https://www.bilibili.com/video/{OTHER}/?p=1", f"https://www.bilibili.com/video/{OTHER}/?p=2", VIDEO,
    ]
    assert [item.title for item in parsed.items] == ["另一门课 · 第一讲", "另一门课 · 第二讲", "本节"]
    assert [item.selected for item in parsed.items] == [False, False, True]


def test_own_parts_take_precedence_over_a_parent_season(monkeypatch):
    mock_view(monkeypatch, {"bvid": BV, "title": "课程", "pages": [FIRST, SECOND],
                            "ugc_season": {"sections": [{"episodes": [{"bvid": OTHER}, {"bvid": BV}]}]}})
    result = catalog.preview_bilibili_catalog(BV)
    assert result.kind == "parts" and [i.selected for i in result.items] == [True, False]


def test_short_link_uses_redirect_part_and_drops_tracking(monkeypatch):
    calls = mock_view(monkeypatch, {"bvid": BV, "title": "课程", "pages": [FIRST, SECOND]}, redirect=VIDEO + "?p=2")
    result = catalog.preview_bilibili_catalog("分享 https://b23.tv/abc?vd_source=secret")
    assert [item.selected for item in result.items] == [False, True]
    assert calls == [("https://b23.tv/abc", None), (catalog._VIEW, {"bvid": BV})]


@pytest.mark.parametrize("source", [VIDEO + "?p=3", VIDEO + "?p=oops", VIDEO + "?p=0"])
def test_invalid_part_never_silently_selects_a_different_episode(monkeypatch, source):
    mock_view(monkeypatch, {"bvid": BV, "title": "课程", "pages": [FIRST, SECOND]})
    with pytest.raises(ValueError, match="分 P"):
        catalog.preview_bilibili_catalog(source)


def test_offsite_shortlink_redirect_is_rejected_before_connecting(monkeypatch):
    calls = mock_view(monkeypatch, {}, redirect=f"http://127.0.0.1/private/{BV}")
    with pytest.raises(ValueError, match="B 站视频"):
        catalog.preview_bilibili_catalog("https://b23.tv/abc")
    assert calls == [("https://b23.tv/abc", None)]


@pytest.mark.parametrize("source", ["https://evil.test/video/" + BV, "example.mp4", "https://b23.tv:9999/abc"])
def test_invalid_sources_make_no_network_requests(monkeypatch, source):
    calls = mock_view(monkeypatch, {})
    with pytest.raises(ValueError):
        catalog.preview_bilibili_catalog(source)
    assert calls == []


def test_single_video_and_missing_title_or_duration(monkeypatch):
    mock_view(monkeypatch, {"bvid": BV, "pages": [{"page": 1}]})
    result = catalog.preview_bilibili_catalog(BV)
    assert result.kind == "single" and result.title == BV
    assert result.items == [catalog.CatalogItem(VIDEO, BV, None, True)]


def test_preview_cache_reuses_bv_but_respects_requested_part_and_expires(monkeypatch):
    clock = [2000.]
    monkeypatch.setattr(catalog, "monotonic", lambda: clock[0])
    view = {"bvid": BV, "title": "原目录", "pages": [FIRST, SECOND]}
    calls = mock_view(monkeypatch, view)
    assert catalog.preview_bilibili_catalog(BV).items[0].selected
    view["title"] = "新目录"
    clock[0] += catalog._TTL_SECONDS - 1
    result = catalog.preview_bilibili_catalog(VIDEO + "?p=2")
    assert result.title == "原目录" and result.items[1].selected and not result.items[0].selected
    clock[0] += 2
    assert catalog.preview_bilibili_catalog(BV).title == "新目录"
    assert len(calls) == 2


def test_incomplete_directories_never_cache_a_partial_list(monkeypatch):
    data = {"bvid": BV, "pages": [FIRST], "videos": 18}
    calls = mock_view(monkeypatch, data)
    with pytest.raises(catalog.CatalogUnavailable, match="分 P 目录不完整"):
        catalog.preview_bilibili_catalog(BV)
    data.update(videos=1, ugc_season={"ep_count": 3, "sections": [{"episodes": [
        {"bvid": OTHER, "pages": [FIRST, SECOND]}, {"bvid": BV, "page": FIRST},
    ]}]})
    # Two eligible episodes (one with two parts) do not make three eligible episodes.
    with pytest.raises(catalog.CatalogUnavailable, match="合集目录不完整"):
        catalog.preview_bilibili_catalog(BV)
    data["ugc_season"]["ep_count"] = 2
    assert catalog.preview_bilibili_catalog(BV).kind == "season"
    assert len(calls) == 3


@pytest.mark.parametrize("view_timeout", [False, True])
def test_pagelist_restores_multipart_titles_on_view_failure_and_caches(monkeypatch, view_timeout):
    calls = mock_view(monkeypatch, None, status=412, pagelist=[FIRST, SECOND], view_timeout=view_timeout)
    result = catalog.preview_bilibili_catalog(VIDEO + "?p=2")
    assert (result.kind, result.title) == ("parts", BV)
    assert [(i.title, i.duration, i.selected) for i in result.items] == [
        ("第一讲", 109, False), ("第二讲", 1358, True),
    ]
    assert catalog.preview_bilibili_catalog(BV).items[0].selected
    with pytest.raises(ValueError, match="分 P"):
        catalog.preview_bilibili_catalog(VIDEO + "?p=3")
    assert calls == [(catalog._VIEW, {"bvid": BV}), (catalog._PAGELIST, {"bvid": BV, "jsonp": "jsonp"})]


@pytest.mark.parametrize("pagelist", [[FIRST], [SECOND, FIRST], [FIRST, FIRST],
                                     [FIRST, {**SECOND, "cid": 0}], {"pages": [FIRST, SECOND]}])
def test_unusable_pagelist_preserves_view_failure_instead_of_inventing_collection(monkeypatch, pagelist):
    calls = mock_view(monkeypatch, None, status=412, pagelist=pagelist)
    with pytest.raises(catalog.CatalogUnavailable, match="HTTP 412"):
        catalog.preview_bilibili_catalog(BV)
    assert len(calls) == 2


@pytest.mark.parametrize(("data", "code", "status"), [
    ({}, -404, 200), ({}, 0, 412), ({"bvid": BV, "pages": []}, 0, 200), ({"bvid": OTHER, "pages": [FIRST]}, 0, 200),
])
def test_inaccessible_catalog_has_no_task_side_effects(tmp_path, monkeypatch, data, code, status):
    mock_view(monkeypatch, data, code=code, status=status)
    app, _, _, _ = build_test_app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/bilibili/catalog", json={"source": BV}).status_code == 502
        assert client.get("/api/tasks").json()["items"] == []
