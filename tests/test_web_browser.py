"""Optional browser checks: V2T_BROWSER_TEST_URL=http://127.0.0.1:5174 pytest tests/test_web_browser.py.

All API traffic is mocked; no video download or ASR is performed.
"""
from __future__ import annotations

import os

import pytest

URL = os.getenv("V2T_BROWSER_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set V2T_BROWSER_TEST_URL to run browser checks")

TASK = dict(id="demo", status="running", source_input="BV1xx411c7XD", provider="faster-whisper",
            model="small", progress_percent=0.4, current_stage="transcribing", current_message="",
            error_message="", video_id=None, created_at="2026-09-28T12:00:00", started_at=None, finished_at=None)
VIDEO = dict(id=1, source_kind="bilibili", source_input=TASK["source_input"],
             source_url="https://www.bilibili.com/video/BV1xx411c7XD", title="线程池笔记",
             engine="faster-whisper", model="small", created_at=TASK["created_at"])
DOCUMENT = dict(video_id=1, title=VIDEO["title"], platform="bilibili", url=VIDEO["source_url"],
                uploader=None, duration=60, engine="faster-whisper", model="small", transcript_source="asr",
                version_kind="original", text="线程池使用阻塞队列。", has_timestamps=True,
                segments=[dict(start=0, end=10, text="线程池使用阻塞队列。")])


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()

        def api(route):
            path = route.request.url.split("/api/")[1].split("?")[0]
            responses = {
                "config": dict(default_provider="faster-whisper", default_model="small",
                               providers=["faster-whisper", "whisper", "sensevoice", "volcengine"],
                               enabled_providers=["faster-whisper"]),
                "tasks": {"items": [TASK, {**TASK, "id": "failed", "status": "failed", "error_message": "识别失败"}]},
                "tasks/demo": TASK,
                "tasks/transcribe": {"task_id": "demo"},
                "tasks/batch": {"items": [TASK, {**TASK, "id": "second"}]},
                "videos": {"items": [VIDEO]},
                "videos/1/document": DOCUMENT,
            }
            route.fulfill(json=responses.get(path, {}))

        page.route("**/api/**", api)
        yield page
        browser.close()


def test_navigation_and_submissions(page):
    from playwright.sync_api import expect

    page.goto(URL)
    nav = page.get_by_role("navigation", name="主导航")
    expect(nav.get_by_role("link", name="新建转写")).to_have_attribute("aria-current", "page")
    nav.get_by_role("link", name="任务", exact=True).click()
    expect(page.get_by_text("处理失败", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("heading", name="任务", exact=True)).to_be_visible()
    nav.get_by_role("link", name="文字稿", exact=True).click()
    page.get_by_role("link", name="线程池笔记", exact=False).click()
    expect(page.get_by_role("heading", name="线程池笔记")).to_be_visible()
    page.get_by_role("link", name="← 全部文字稿").click()
    expect(page).to_have_url(f"{URL}/videos")
    page.go_back()
    expect(page.get_by_role("heading", name="线程池笔记")).to_be_visible()
    nav.get_by_role("link", name="新建转写").click()
    page.get_by_label("粘贴链接").fill("BV1xx411c7XD")
    page.get_by_role("button", name="转成文字").click()
    expect(page).to_have_url(f"{URL}/tasks/demo")
    nav.get_by_role("link", name="新建转写").click()
    page.get_by_label("粘贴链接").fill("BV1xx411c7XD\nBV1yy411c7XD")
    page.get_by_role("button", name="转成文字").click()
    expect(page).to_have_url(f"{URL}/tasks")


def test_completion_navigation(page):
    from playwright.sync_api import expect

    page.route("**/api/tasks/demo", lambda route: route.fulfill(json={**TASK, "status": "completed", "video_id": 1}))
    page.goto(f"{URL}/tasks/demo")
    expect(page).to_have_url(f"{URL}/videos/1")
    expect(page.get_by_text("转写完成，文字稿已保存。")).to_be_visible()


def test_task_polling_recovers_without_losing_progress(page):
    from playwright.sync_api import expect

    phase = "initial"
    def response(route):
        if phase == "offline":
            route.fulfill(status=503, json={"detail": "offline"})
        else:
            route.fulfill(json={**TASK, "progress_percent": 0.4 if phase == "initial" else 0.6})
    page.route("**/api/tasks/demo", response)
    page.goto(f"{URL}/tasks/demo")
    expect(page.get_by_text("40%", exact=True)).to_be_visible()
    phase = "offline"
    expect(page.get_by_text("连接中断，正在重试", exact=False)).to_be_visible()
    expect(page.get_by_text("40%", exact=True)).to_be_visible()
    phase = "recovered"
    expect(page.get_by_text("60%", exact=True)).to_be_visible(timeout=10000)
    expect(page.get_by_text("连接中断，正在重试", exact=False)).to_have_count(0)


def test_missing_task_stops_polling(page):
    from playwright.sync_api import expect

    calls = []
    def missing(route):
        calls.append(1)
        route.fulfill(status=404, json={"detail": "task not found"})
    page.route("**/api/tasks/missing", missing)
    page.goto(f"{URL}/tasks/missing")
    expect(page.get_by_role("heading", name="找不到这个任务")).to_be_visible()
    initial_count = len(calls)
    page.wait_for_timeout(1500)
    assert len(calls) == initial_count


def test_library_error_is_not_empty_state(page):
    from playwright.sync_api import expect

    page.route("**/api/videos*", lambda route: route.fulfill(status=500, json={"detail": "offline"}))
    page.goto(f"{URL}/videos")
    expect(page.get_by_text("文字稿加载失败", exact=False)).to_be_visible()
    expect(page.get_by_text("还没有文字稿", exact=False)).to_have_count(0)
