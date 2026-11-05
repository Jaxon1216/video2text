from __future__ import annotations

import json
import os
from collections import OrderedDict
from dataclasses import dataclass, replace
from threading import Lock
from time import monotonic
from typing import Literal
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

from v2t.inputs import BV_PATTERN, extract_first_url, parse_source
from v2t.models import SourceRef

_VIEW = "https://api.bilibili.com/x/web-interface/view"
_PAGELIST = "https://api.bilibili.com/x/player/pagelist"
_SHORT_HOSTS = {"b23.tv", "www.b23.tv", "bili2233.cn", "www.bili2233.cn"}
_VIDEO_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
_MAX_BYTES = 8 * 1024 * 1024
_TIMEOUT = (3, 5)
_TTL_SECONDS = 300
_CACHE_LIMIT = 16


class CatalogUnavailable(RuntimeError):
    """The public directory could not be read; normal link submission can still be offered."""


class _EndpointUnavailable(CatalogUnavailable):
    """A transport or service failure may be specific to one metadata endpoint."""


@dataclass
class CatalogItem:
    source: str
    title: str
    duration: int | None
    selected: bool


@dataclass
class Catalog:
    kind: Literal["single", "parts", "season"]
    title: str
    items: list[CatalogItem]


# Only sanitized, successfully parsed public directories are cached.
_catalog_cache: OrderedDict[str, tuple[float, Catalog]] = OrderedDict()
_cache_lock = Lock()


def _host(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.username is not None or parts.port not in {None, 80, 443}:
        raise ValueError("请输入 B 站视频链接或 BV 号")
    return parts.hostname or ""


def _resolve_input(raw: str, session: requests.Session) -> SourceRef:
    url = extract_first_url(raw)
    if not url:
        if BV_PATTERN.fullmatch(raw.strip()):
            return parse_source(raw.strip())
        raise ValueError("请输入 B 站视频链接或 BV 号")
    requested = parse_qs(urlsplit(url).query, keep_blank_values=True).get("p")
    # Resolve short-link locations without following a redirect off of Bilibili.
    for _ in range(3):
        if _host(url) not in _SHORT_HOSTS:
            break
        short = urlsplit(url)
        with session.get(f"https://{short.hostname}{short.path}", timeout=_TIMEOUT,
                         stream=True, allow_redirects=False) as response:
            if not response.is_redirect:
                raise CatalogUnavailable("无法展开 B 站短链，请复制视频页链接")
            url = urljoin(short.geturl(), response.headers["Location"])
    if _host(url) not in _VIDEO_HOSTS:
        raise ValueError("请输入 B 站视频链接或 BV 号")
    parts = urlsplit(url)
    path = parts.path.strip("/").split("/")
    if len(path) != 2 or path[0] != "video" or not BV_PATTERN.fullmatch(path[1]):
        raise ValueError("链接中没有有效的 B 站视频 BV 号")
    canonical = f"https://www.bilibili.com/video/{path[1]}/"
    if requested is None:
        requested = parse_qs(parts.query, keep_blank_values=True).get("p")
    if requested is not None:
        if len(requested) != 1 or not requested[0].isascii() or not requested[0].isdecimal() or len(requested[0]) > 9:
            raise ValueError("分 P 编号应为正整数")
        number = int(requested[0])
        if number < 1:
            raise ValueError("分 P 编号应为正整数")
        canonical += f"?p={number}"
    return parse_source(canonical)


def _read_data(session: requests.Session, url: str, params: dict[str, str]) -> object:
    with session.get(url, params=params, timeout=_TIMEOUT, stream=True, allow_redirects=False) as response:
        if response.status_code != 200:
            error = f"B 站目录请求失败（HTTP {response.status_code}）"
            if response.status_code in {412, 429} or response.status_code >= 500:
                raise _EndpointUnavailable(error)
            raise CatalogUnavailable(error)
        content = bytearray()
        for chunk in response.iter_content(64 * 1024):
            content.extend(chunk)
            if len(content) > _MAX_BYTES:
                raise CatalogUnavailable("B 站目录过大，请使用分集链接")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _EndpointUnavailable("B 站没有返回有效的目录") from exc
    if isinstance(payload, dict) and payload.get("code") in (-352, -412, -500, -503, -504, -509):
        raise _EndpointUnavailable("B 站暂时无法提供视频目录")
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise CatalogUnavailable("B 站未提供该视频的公开目录")
    return payload.get("data")


def _rows(values: object) -> list[dict]:
    return [v for v in values if isinstance(v, dict)] if isinstance(values, list) else []


def _label(value: object, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _item(bv: str, page: int | None, title: str, duration: object, original: SourceRef) -> CatalogItem:
    source = f"https://www.bilibili.com/video/{bv}/"
    if page is not None:
        source += f"?p={page}"
    return CatalogItem(
        source=source, title=title,
        duration=duration if type(duration) is int and duration >= 0 else None,
        selected=bv == original.bv and (page or 1) == (original.page or 1),
    )


def _list_parts(bv: str, pages: list[dict], original: SourceRef, episode_title: str = "") -> list[CatalogItem]:
    parts = []
    seen: set[int] = set()
    for page in pages:
        number = page.get("page")
        if type(number) is not int or number < 1 or number in seen:
            continue
        seen.add(number)
        label = _label(page.get("part"), f"第 {number} P")
        if episode_title:
            label = f"{episode_title} · {label}"
        parts.append(_item(bv, number, label, page.get("duration"), original))
    return parts


def _season_items(season: dict, original: SourceRef) -> list[CatalogItem]:
    items: dict[str, CatalogItem] = {}
    usable_episodes = 0
    for section in _rows(season.get("sections")):
        for episode in _rows(section.get("episodes")):
            bv = episode.get("bvid")
            if not isinstance(bv, str) or not BV_PATTERN.fullmatch(bv):
                continue
            arc = episode.get("arc") if isinstance(episode.get("arc"), dict) else {}
            title = _label(episode.get("title"), _label(arc.get("title"), bv))
            pages = _rows(episode.get("pages"))
            if len(pages) > 1:
                entries = _list_parts(bv, pages, original, title)
                if len(entries) != len(pages):
                    raise CatalogUnavailable("B 站返回的合集目录不完整")
            else:
                page = episode.get("page")
                if not isinstance(page, dict) or not page:
                    page = pages[0] if pages else {}
                number = page.get("page") or 1
                if type(number) is not int or number < 1:
                    continue
                entries = [_item(bv, number if number > 1 else None, title,
                                 page.get("duration", arc.get("duration")), original)]
            usable_episodes += 1
            for entry in entries:
                # The bare BV and explicit P1 identify the same playable clip.
                items.setdefault(entry.source.removesuffix("?p=1"), entry)
    declared = season.get("ep_count")
    if type(declared) is int and declared > usable_episodes:
        raise CatalogUnavailable("B 站返回的合集目录不完整")
    return list(items.values())


def _catalog(data: dict, source: SourceRef) -> Catalog:
    bv = source.bv
    if not bv or data.get("bvid") != bv:
        raise CatalogUnavailable("B 站返回的目录与视频不匹配")
    title = _label(data.get("title"), bv)
    pages = _rows(data.get("pages"))
    if not pages:
        raise CatalogUnavailable("B 站未提供该视频的分 P 目录")
    items = _list_parts(bv, pages, source)
    declared = data.get("videos")
    if len(items) != len(data["pages"]) or (type(declared) is int and declared > len(items)):
        raise CatalogUnavailable("B 站返回的分 P 目录不完整")
    if len(items) > 1:
        # A multipart submission can also belong to a season. Keep its own table of contents.
        return Catalog("parts", title, items)
    season = data.get("ugc_season")
    if isinstance(season, dict):
        items = _season_items(season, source)
        if len(items) > 1 and any(item.selected for item in items):
            return Catalog("season", _label(season.get("title"), title), items)
    return Catalog("single", title, [_item(bv, None, title, data.get("duration", pages[0].get("duration")), source)])


def _load_catalog(session: requests.Session, bv: str) -> Catalog:
    with _cache_lock:
        cached = _catalog_cache.get(bv)
        if cached and cached[0] > monotonic():
            _catalog_cache.move_to_end(bv)
            return cached[1]
    try:
        data = _read_data(session, _VIEW, {"bvid": bv})
    except (requests.RequestException, _EndpointUnavailable) as view_error:
        # The public pagelist can recover multipart videos, but cannot identify cross-BV seasons.
        try:
            pages = _read_data(session, _PAGELIST, {"bvid": bv, "jsonp": "jsonp"})
        except (requests.RequestException, CatalogUnavailable):
            raise view_error from None
        if not isinstance(pages, list) or len(pages) < 2:
            raise view_error
        for number, page in enumerate(pages, 1):
            if (not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] != number
                    or type(page.get("cid")) is not int or page["cid"] < 1):
                raise view_error
        data = {"bvid": bv, "pages": pages}
    if not isinstance(data, dict):
        raise CatalogUnavailable("B 站未提供该视频的公开目录")
    result = _catalog(data, parse_source(f"https://www.bilibili.com/video/{bv}/"))
    with _cache_lock:
        _catalog_cache[bv] = (monotonic() + _TTL_SECONDS, result)
        _catalog_cache.move_to_end(bv)
        if len(_catalog_cache) > _CACHE_LIMIT:
            _catalog_cache.popitem(last=False)
    return result


def preview_bilibili_catalog(raw: str) -> Catalog:
    """Read public episode metadata; no media, subtitles or transcription tasks are created."""
    try:
        with requests.Session() as session:
            # Catalog previews use public metadata; do not import browser cookies or netrc credentials.
            session.trust_env = False
            if os.getenv("V2T_USE_PROXY", "").strip().lower() in {"true", "yes", "1", "on"}:
                session.proxies.update(requests.utils.get_environ_proxies(_VIEW))
            session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
            source = _resolve_input(raw, session)
            assert source.bv is not None
            directory = _load_catalog(session, source.bv)
    except requests.RequestException as exc:
        raise CatalogUnavailable("无法连接 B 站目录，请稍后重试") from exc
    number = source.page or 1
    if directory.kind != "parts":
        if number != 1:
            raise ValueError("链接指定的分 P 不在 B 站返回的目录中")
        return directory
    selected = f"https://www.bilibili.com/video/{source.bv}/?p={number}"
    items = [replace(item, selected=item.source == selected) for item in directory.items]
    if not any(item.selected for item in items):
        raise ValueError("链接指定的分 P 不在 B 站返回的目录中")
    return replace(directory, items=items)
