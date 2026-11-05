"""Optional browser checks: V2T_BROWSER_TEST_URL=http://127.0.0.1:5174 pytest tests/test_web_browser.py.

All API traffic is mocked; no video download or ASR is performed.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest

URL = os.getenv("V2T_BROWSER_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set V2T_BROWSER_TEST_URL to run browser checks")

TASK = dict(id="demo", status="running", source_input="BV1xx411c7XD", provider="faster-whisper",
            model="small", progress_percent=0.4, current_stage="transcribing", current_message="",
            error_message="", video_id=None, created_at="2026-09-28T12:00:00", started_at=None, finished_at=None)
VIDEO = dict(id=1, source_kind="bilibili", source_input=TASK["source_input"],
             source_url="https://www.bilibili.com/video/BV1xx411c7XD", title="线程池笔记",
             engine="faster-whisper", model="small", created_at=TASK["created_at"])
DOCUMENT = dict(video_id=1, version_id=1, ai_output_path="/tmp/video2text/transcripts/ai/video-1.txt",
                title=VIDEO["title"], platform="bilibili", url=VIDEO["source_url"],
                uploader=None, duration=60, engine="faster-whisper", model="small", transcript_source="asr",
                version_kind="original", text="线程池使用阻塞队列。", has_timestamps=True,
                segments=[dict(start=0, end=10, text="线程池使用阻塞队列。")])


@pytest.fixture
def page(request):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("V2T_BROWSER_CHANNEL", "chrome"), headless=True, timeout=20000)
        page = browser.new_page(**getattr(request, "param", {}))
        page.set_default_timeout(10000)

        def api(route):
            path = route.request.url.split("/api/")[1].split("?")[0]
            responses = {
                "config": dict(default_provider="faster-whisper", default_model="small",
                               providers=["faster-whisper", "whisper", "qwen3-asr", "sensevoice", "volcengine"],
                               enabled_providers=["faster-whisper"]),
                "models": {"items": [
                    {"provider": "faster-whisper", "default_model": "small", "enabled": True,
                     "models": [{"id": value, "label": value} for value in ["large-v3-turbo", "small"]]},
                    {"provider": "whisper", "default_model": "small", "enabled": False,
                     "models": [{"id": "small", "label": "small"}]},
                    {"provider": "qwen3-asr", "default_model": "Qwen/Qwen3-ASR-1.7B", "enabled": False,
                     "models": [{"id": "Qwen/Qwen3-ASR-1.7B", "label": "Qwen3-ASR 1.7B"}]},
                    {"provider": "sensevoice", "default_model": "", "enabled": False, "models": []},
                    {"provider": "volcengine", "default_model": "bigmodel", "enabled": False,
                     "models": [{"id": "bigmodel", "label": "bigmodel"}]},
                ]},
                "tasks": {"items": [TASK, {**TASK, "id": "failed", "status": "failed", "error_message": "识别失败"}]},
                "tasks/demo": TASK,
                "tasks/transcribe": {"task_id": "demo"},
                "tasks/batch": {"items": [TASK, {**TASK, "id": "second"}]},
                "bilibili/catalog": {"kind": "single", "title": "单条视频", "items": []},
                "videos": {"items": [VIDEO]},
                "videos/1/document": DOCUMENT,
                "videos/1/screenshots": {"status": "disabled", "reason": None, "sampled_count": 0, "images": []},
                "videos/1/versions": {"items": [{"id": 1, "kind": "original", "is_active": True}],
                                      "ai_output_path": DOCUMENT["ai_output_path"], "ai_import_error": None},
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
    expect(page.get_by_role("checkbox", name="为文字稿配图")).not_to_be_checked()
    with page.expect_request("**/api/tasks/transcribe") as single:
        page.get_by_role("button", name="转成文字").click()
    assert single.value.post_data_json["include_screenshots"] is False
    expect(page).to_have_url(f"{URL}/tasks/demo")
    nav.get_by_role("link", name="新建转写").click()
    page.get_by_label("粘贴链接").fill("BV1xx411c7XD\nBV1yy411c7XD")
    page.get_by_role("checkbox", name="为文字稿配图").check()
    with page.expect_request("**/api/tasks/batch") as batch:
        page.get_by_role("button", name="转成文字").click()
    assert batch.value.post_data_json["include_screenshots"] is True
    expect(page).to_have_url(f"{URL}/tasks")


def test_completion_navigation(page):
    from playwright.sync_api import expect

    page.route("**/api/tasks/demo", lambda route: route.fulfill(json={**TASK, "status": "completed", "video_id": 1}))
    page.goto(f"{URL}/tasks/demo")
    expect(page).to_have_url(f"{URL}/videos/1")
    expect(page.get_by_text("转写完成，文字稿已保存。")).to_be_visible()


def test_youtube_submission_platform_copy_and_timestamp_link(page):
    from playwright.sync_api import expect

    youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    submitted = []
    page.route("**/api/tasks/transcribe", lambda route: (submitted.append(route.request.post_data_json),
                                                        route.fulfill(json={"task_id": "demo"})))
    page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.copied = text}}})")
    page.goto(URL)
    expect(page.get_by_label("粘贴链接")).to_have_attribute("placeholder", re.compile("YouTube"))
    page.get_by_label("粘贴链接").fill(youtube_url)
    page.get_by_role("checkbox", name="为文字稿配图").check()
    page.get_by_role("button", name="转成文字").click()
    expect(page).to_have_url(f"{URL}/tasks/demo")
    assert submitted[0]["source"] == youtube_url and submitted[0]["include_screenshots"] is True
    page.route("**/api/videos", lambda route: route.fulfill(json={"items": [{**VIDEO, "source_kind": "youtube", "source_url": youtube_url}]}))
    page.goto(f"{URL}/videos")
    expect(page.get_by_text("YouTube", exact=True)).to_be_visible()
    page.get_by_role("button", name="复制线程池笔记的原视频链接").click(force=True)
    assert page.evaluate("window.copied") == youtube_url
    page.route("**/api/videos/1/document*", lambda route: route.fulfill(json={**DOCUMENT, "platform": "youtube", "url": youtube_url}))
    page.goto(f"{URL}/videos/1")
    expect(page.locator("a.stamp")).to_have_attribute("href", f"{youtube_url}&t=0")
    expect(page.locator("a.stamp")).to_have_attribute("title", "在 YouTube 从这里播放")


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


def test_failed_task_can_retry_again_after_a_second_failure(page):
    from playwright.sync_api import expect

    submitted = []
    page.on("request", lambda request: submitted.append(request) if request.method == "POST" else None)

    def task(route):
        task_id = route.request.url.rsplit("/", 1)[1]
        route.fulfill(json={**TASK, "id": task_id, "status": "failed", "error_message": f"{task_id} 识别失败"})

    def retry(route):
        task_id = route.request.url.rsplit("/", 2)[1]
        next_id = "second-failure" if task_id == "first-failure" else "third-attempt"
        route.fulfill(json={"task_id": next_id, "status": "queued"})

    page.route("**/api/tasks/first-failure", task)
    page.route("**/api/tasks/second-failure", task)
    page.route("**/api/tasks/third-attempt", lambda route: route.fulfill(json={**TASK, "id": "third-attempt"}))
    page.route("**/api/tasks/*/retry", retry)
    page.goto(f"{URL}/tasks/first-failure")
    page.get_by_role("button", name="重试", exact=True).click()
    expect(page).to_have_url(f"{URL}/tasks/second-failure")
    expect(page.get_by_text("second-failure 识别失败", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="重试", exact=True)).to_be_enabled()
    page.get_by_role("button", name="重试", exact=True).click()
    expect(page).to_have_url(f"{URL}/tasks/third-attempt")
    expect(page.get_by_text("40%", exact=True)).to_be_visible()

    assert [request.url for request in submitted] == [
        f"{URL}/api/tasks/first-failure/retry",
        f"{URL}/api/tasks/second-failure/retry",
    ]
    assert all(request.post_data is None for request in submitted)


def test_cancel_running_task_from_detail(page):
    from playwright.sync_api import expect

    page.route("**/api/tasks/demo/cancel", lambda route: route.fulfill(json={**TASK, "cancel_requested": True}))
    page.goto(f"{URL}/tasks/demo")
    page.get_by_role("button", name="中断任务").click()
    expect(page.get_by_text("正在中断", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="中断任务")).to_have_count(0)


def test_cancel_queued_task_from_list_without_navigation(page):
    from playwright.sync_api import expect

    queued = {**TASK, "status": "queued", "current_stage": "queued", "id": "queued",
              "created_at": datetime.now().isoformat()}
    page.route("**/api/tasks", lambda route: route.fulfill(json={"items": [queued]}))
    page.route("**/api/tasks/queued/cancel", lambda route: route.fulfill(json={**queued, "status": "cancelled", "cancel_requested": True}))
    page.goto(f"{URL}/tasks")
    page.get_by_role("button", name="中断任务").click()
    expect(page).to_have_url(f"{URL}/tasks")
    expect(page.get_by_text("已取消", exact=True)).to_be_visible()


def test_library_error_is_not_empty_state(page):
    from playwright.sync_api import expect

    page.route("**/api/videos*", lambda route: route.fulfill(status=500, json={"detail": "offline"}))
    page.goto(f"{URL}/videos")
    expect(page.get_by_text("文字稿加载失败", exact=False)).to_be_visible()
    expect(page.get_by_text("还没有文字稿", exact=False)).to_have_count(0)


@pytest.mark.parametrize('label,extension', [('TXT', 'txt'), ('Markdown', 'md'), ('SRT', 'srt')])
def test_export_confirmation_and_focus(page, label, extension):
    from playwright.sync_api import expect

    requests = []
    def export(route):
        requests.append(route.request.url)
        route.fulfill(body="transcript", content_type="text/plain", headers={"Content-Disposition": f'attachment; filename="demo.{extension}"'})
    page.route("**/api/videos/1/export?**", export)
    page.goto(f"{URL}/videos/1")
    trigger = page.get_by_role("button", name=label, exact=True)
    trigger.click()
    dialog = page.get_by_role("dialog", name="确认下载")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("线程池笔记", exact=True)).to_be_visible()
    assert requests == []
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(trigger).to_be_focused()
    trigger.click()
    dialog.get_by_role("button", name="取消").click()
    assert requests == []
    trigger.click()
    with page.expect_download():
        dialog.get_by_role("link", name="确认下载").click()
    assert len(requests) == 1 and f"format={extension}" in requests[0]
    expect(dialog).to_have_count(0)


def test_srt_unavailable_without_timestamps(page):
    from playwright.sync_api import expect

    page.route("**/api/videos/1/document*", lambda route: route.fulfill(json={**DOCUMENT, "has_timestamps": False, "segments": []}))
    page.goto(f"{URL}/videos/1")
    expect(page.get_by_text("SRT", exact=True)).to_have_attribute("title", "没有时间戳，无法导出字幕")
    expect(page.get_by_role("button", name="SRT", exact=True)).to_have_count(0)


@pytest.mark.parametrize("page", [{"viewport": {"width": 1280, "height": 900}},
                                  {"viewport": {"width": 390, "height": 844}}], indirect=True)
def test_screenshots_follow_version_and_open_full_image(page):
    from playwright.sync_api import expect

    segments = [*DOCUMENT["segments"], dict(start=75, end=95, text="第二段示例。")]
    ai_text = "已优化的两段示例。"
    image_url = "/api/videos/1/screenshots/1"
    page.route("**/api/videos/1/versions", lambda route: route.fulfill(json={
        "items": [{"id": 1, "kind": "original", "is_active": True},
                  {"id": 2, "kind": "ai_optimized", "is_active": False}],
        "ai_output_path": DOCUMENT["ai_output_path"], "ai_import_error": None,
    }))

    def respond_document(route):
        version = int(parse_qs(urlsplit(route.request.url).query)["version_id"][0])
        document = {**DOCUMENT, "segments": segments} if version == 1 else {
            **DOCUMENT, "version_id": 2, "version_kind": "ai_optimized", "segments": [],
            "has_timestamps": False, "text": ai_text,
        }
        route.fulfill(json=document)

    def respond_manifest(route):
        version = int(parse_qs(urlsplit(route.request.url).query)["version_id"][0])
        route.fulfill(json={"status": "ready", "sampled_count": 2, "reason": None,
                            "images": [{"index": 1, "time": 80, "block_index": 1 if version == 1 else None,
                                        "url": image_url}]})

    page.route("**/api/videos/1/document*", respond_document)
    page.route("**/api/videos/1/screenshots?*", respond_manifest)
    def respond_image(route):
        route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="8" height="4"/>')

    page.route(f"**{image_url}", respond_image)
    page.context.route(f"**{image_url}", respond_image)
    page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.copied = text}}})")
    page.goto(f"{URL}/videos/1")
    expect(page.locator(".passage").nth(0).locator("img")).to_have_count(0)
    image = page.locator(".passage").nth(1).get_by_role("link", name="查看 01:20 的原图")
    expect(image).to_have_attribute("href", image_url)
    assert image.locator("img").evaluate("async el => { await el.decode(); return el.naturalWidth; }") == 8
    with page.expect_popup() as full_image:
        image.click()
    expect(full_image.value).to_have_url(f"{URL}{image_url}")
    full_image.value.close()
    page.get_by_role("button", name="复制全文").click()
    assert page.evaluate("window.copied") == "[00:00] 线程池使用阻塞队列。\n\n[01:15] 第二段示例。"
    page.get_by_role("tab", name="AI 优化").click()
    expect(page.locator(".transcript")).to_have_text(ai_text)
    expect(page.get_by_role("region", name="截图时间线").get_by_role("img")).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="下载图文包").click()
    expect(page.get_by_role("link", name="确认下载")).to_have_attribute("href", "/api/videos/1/illustrated.zip?version_id=2")
    page.keyboard.press("Escape")
    page.get_by_role("tab", name="原始稿").click()
    expect(page.get_by_role("region", name="截图时间线")).to_have_count(0)
    expect(page.locator(".passage").nth(1).get_by_role("img")).to_have_count(1)


def test_text_remains_readable_while_screenshots_pending_or_failed(page):
    from playwright.sync_api import expect

    status = "pending"
    page.route("**/api/videos/1/screenshots?*", lambda route: route.fulfill(json={
        "status": status, "reason": "只有音频轨", "sampled_count": 0, "images": [],
    }))
    page.route("**/api/tasks/demo", lambda route: route.fulfill(json={
        **TASK, "status": "cancelled", "include_screenshots": True, "video_id": 1,
    }))
    page.goto(f"{URL}/tasks/demo")
    expect(page.get_by_role("button", name="重试", exact=True)).to_be_visible()
    page.get_by_role("link", name="打开已保存的文字稿").click()
    expect(page.get_by_text("配图处理中，可以先阅读正文。")).to_be_visible()
    expect(page.locator(".transcript")).to_contain_text(DOCUMENT["text"])
    status = "failed"
    expect(page.get_by_text("未能生成配图：只有音频轨。")).to_be_visible()
    expect(page.locator(".transcript")).to_contain_text(DOCUMENT["text"])
    expect(page.get_by_role("button", name="下载图文包")).to_have_count(0)


@pytest.mark.parametrize("page", [{"viewport": {"width": 1280, "height": 900}},
                                  {"viewport": {"width": 390, "height": 844}}], indirect=True)
def test_ai_tabs_copy_export_and_refresh_use_selected_version(page):
    from playwright.sync_api import expect

    latest_id = 2
    documents = {1: DOCUMENT,
                 2: {**DOCUMENT, "version_id": 2, "version_kind": "ai_optimized", "has_timestamps": False,
                     "segments": [], "text": "线程池使用工作队列，复用工作线程。"},
                 3: {**DOCUMENT, "version_id": 3, "version_kind": "ai_optimized", "has_timestamps": False,
                     "segments": [], "text": "更新后的 AI 优化稿。"}}
    page.route("**/api/videos/1/versions", lambda route: route.fulfill(json={
        "items": [{"id": latest_id, "kind": "ai_optimized", "is_active": False},
                  {"id": 1, "kind": "original", "is_active": True}],
        "ai_output_path": DOCUMENT["ai_output_path"], "ai_import_error": None,
    }))
    page.route("**/api/videos/1/document*", lambda route: route.fulfill(
        json=documents[int(parse_qs(urlsplit(route.request.url).query).get("version_id", ["1"])[0])]))
    exports = []
    posts = []
    page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
    def export(route):
        exports.append(route.request.url)
        route.fulfill(body=documents[latest_id]["text"], content_type="text/plain",
                      headers={"Content-Disposition": 'attachment; filename="demo.txt"'})
    page.route("**/api/videos/1/export?**", export)
    page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.copied = text}}})")
    page.goto(f"{URL}/videos/1")
    original_tab = page.get_by_role("tab", name="原始稿", exact=True)
    ai_tab = page.get_by_role("tab", name="AI 优化", exact=True)
    expect(original_tab).to_have_attribute("aria-selected", "true")
    expect(page.locator("a.stamp")).to_be_visible()
    page.get_by_role("button", name="Copy for AI").click()
    original_copy = page.evaluate("window.copied")
    assert DOCUMENT["ai_output_path"] in original_copy
    assert "以 UTF-8 保存到" in original_copy
    assert "[00:00]" in original_copy
    original_tab.focus()
    page.keyboard.press("ArrowRight")
    expect(ai_tab).to_have_attribute("aria-selected", "true")
    expect(ai_tab).to_be_focused()
    expect(page.locator(".transcript")).to_have_text(documents[2]["text"])
    expect(page.locator("a.stamp")).to_have_count(0)
    expect(page.get_by_role("button", name="SRT", exact=True)).to_have_count(0)
    page.get_by_role("button", name="复制全文").click()
    assert page.evaluate("window.copied") == documents[2]["text"]
    page.get_by_role("button", name="Copy for AI").click()
    assert "文字稿来源：AI 优化稿" in page.evaluate("window.copied")
    page.get_by_role("button", name="TXT", exact=True).click()
    dialog = page.get_by_role("dialog", name="确认下载")
    expect(dialog.get_by_role("link", name="确认下载")).to_have_attribute("href", "/api/videos/1/export?format=txt&version_id=2")
    with page.expect_download():
        dialog.get_by_role("link", name="确认下载").click()
    assert parse_qs(urlsplit(exports[0]).query)["version_id"] == ["2"]
    latest_id = 3
    page.get_by_role("button", name="刷新稿件", exact=True).click()
    expect(page.locator(".transcript")).to_have_text(documents[3]["text"])
    original_tab.click()
    expect(page.locator("a.stamp")).to_be_visible()
    expect(page.locator(".transcript")).to_contain_text(DOCUMENT["text"])
    assert posts == []
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_missing_ai_tab_explains_delivery_path_and_hides_copy_actions(page):
    from playwright.sync_api import expect

    page.goto(f"{URL}/videos/1")
    page.get_by_role("tab", name="AI 优化", exact=True).click()
    expect(page.get_by_role("heading", name="还没有 AI 优化稿")).to_be_visible()
    expect(page.locator(".ai-empty code")).to_have_text(DOCUMENT["ai_output_path"])
    expect(page.get_by_role("button", name="复制全文")).to_have_count(0)
    page.get_by_role("tab", name="原始稿", exact=True).click()
    expect(page.locator("a.stamp")).to_be_visible()


def test_copy_link_does_not_navigate_and_local_has_no_button(page):
    from playwright.sync_api import expect

    page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.copied = text}}})")
    page.route("**/api/videos", lambda route: route.fulfill(json={"items": [VIDEO, {**VIDEO, "id": 2, "title": "本地笔记", "source_kind": "audio", "source_url": None}]}))
    page.goto(f"{URL}/videos")
    button = page.get_by_role("button", name="复制线程池笔记的原视频链接")
    expect(button).to_have_count(1)
    button.focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{URL}/videos")
    expect(page.get_by_role("status")).to_have_text("已复制原视频链接")
    assert page.evaluate("window.copied") == VIDEO["source_url"]
    expect(page.get_by_role("button", name="复制本地笔记的原视频链接")).to_have_count(0)


def test_copy_link_failure_is_visible(page):
    from playwright.sync_api import expect

    page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async () => {throw Error('denied')}}}); document.execCommand = () => false")
    page.goto(f"{URL}/videos")
    button = page.get_by_role("button", name="复制线程池笔记的原视频链接")
    expect(button).to_have_count(1)
    button.focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("status")).to_contain_text("浏览器不允许复制")


def test_switching_provider_selects_its_own_model(page):
    from playwright.sync_api import expect

    page.goto(URL)
    page.get_by_role("button", name="识别选项", exact=False).click()
    model = page.get_by_role("combobox", name="模型", exact=True)
    expect(model).to_be_visible()
    model.select_option("large-v3-turbo")
    engine = page.get_by_role("combobox", name="识别引擎")
    expect(engine.locator("option[value='qwen3-asr']")).to_have_text("Qwen3-ASR")
    engine.select_option("volcengine")
    expect(model).to_have_value("bigmodel")
    expect(page.get_by_text("这个引擎尚未在配置里启用。")).to_be_visible()
    expect(page.get_by_text("云端模型无需本地文件。")).to_be_visible()
    expect(model.get_by_role("option", name="large-v3-turbo", exact=False)).to_have_count(0)
    page.get_by_role("combobox", name="识别引擎").select_option("sensevoice")
    page.get_by_label("粘贴链接").fill("BV1xx411c7XD")
    expect(page.get_by_role("button", name="转成文字")).to_be_disabled()
    expect(page.get_by_text("请先配置 SenseVoice 本地模型目录", exact=False)).to_be_visible()


def test_cache_labels_refresh_and_missing_sensevoice(page):
    from playwright.sync_api import expect

    def models(route):
        refreshed = 'refresh=true' in route.request.url
        route.fulfill(json={'items': [
            {'provider': 'faster-whisper', 'default_model': 'small', 'enabled': True, 'dependency_installed': True,
             'models': [{'id': 'small', 'label': 'small', 'cache_status': 'found', 'cache_message': '关键文件已找到', 'downloadable': True},
                        {'id': 'large-v3-turbo', 'label': 'large-v3-turbo', 'cache_status': 'found' if refreshed else 'missing', 'cache_message': '', 'downloadable': True}]},
            {'provider': 'sensevoice', 'default_model': '/missing', 'enabled': False, 'dependency_installed': False,
             'models': [{'id': '/missing', 'label': '/missing', 'cache_status': 'missing', 'cache_message': '模型目录不存在', 'downloadable': False}]},
        ]})
    page.route('**/api/models*', models)
    page.goto(URL)
    page.get_by_role('button', name='识别选项', exact=False).click()
    select = page.get_by_role('combobox', name='模型', exact=True)
    select.select_option('large-v3-turbo')
    expect(select.get_by_role('option', name='large-v3-turbo', exact=False)).to_contain_text('未下载')
    expect(page.get_by_text('首次识别需要下载模型', exact=False)).to_be_visible()
    page.get_by_label('粘贴链接').fill('BV1xx411c7XD')
    expect(page.get_by_role('button', name='转成文字')).to_be_enabled()
    page.get_by_role('button', name='刷新本地模型状态').click()
    expect(select.get_by_role('option', name='large-v3-turbo', exact=False)).to_contain_text('文件已找到')
    expect(select).to_have_value('large-v3-turbo')
    page.get_by_role('combobox', name='识别引擎').select_option('sensevoice')
    expect(page.get_by_role('button', name='转成文字')).to_be_disabled()
    expect(page.get_by_text('请先配置 SenseVoice 本地模型目录', exact=False)).to_be_visible()


@pytest.mark.parametrize('page', [{'viewport': {'width': 390, 'height': 844}, 'has_touch': True}], indirect=True)
def test_mobile_library_keeps_metadata_readable_and_copy_visible(page):
    from playwright.sync_api import expect

    page.emulate_media(reduced_motion='reduce')
    page.goto(f'{URL}/videos')
    expect(page.get_by_role('link', name='线程池笔记', exact=False)).to_be_visible()
    expect(page.get_by_role('button', name='复制线程池笔记的原视频链接')).to_have_css('opacity', '1')
    tag = page.locator('.library-meta .tag')
    assert tag.bounding_box()['height'] < 30
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


def test_retranscribe_from_video_detail_selects_model_and_opens_new_task(page):
    from playwright.sync_api import expect

    submitted = []
    def create(route):
        submitted.append(route.request.post_data_json)
        route.fulfill(json={"task_id": "new-task", "status": "queued"})
    page.route("**/api/videos/1/retranscribe", create)
    page.goto(f"{URL}/videos/1")
    page.get_by_role("button", name="换模型重转写").click()
    page.get_by_role("combobox", name="模型").select_option("large-v3-turbo")
    page.get_by_role("textbox", name="术语提示").fill("线程池")
    page.get_by_role("button", name="开始重转写").click()
    expect(page).to_have_url(f"{URL}/tasks/new-task")
    assert submitted == [{"provider": "faster-whisper", "model": "large-v3-turbo", "prompt": "线程池"}]


def test_qwen_model_can_be_selected_for_retranscription(page):
    from playwright.sync_api import expect

    submitted = []
    page.route("**/api/videos/1/retranscribe", lambda route: (submitted.append(route.request.post_data_json),
                                                              route.fulfill(json={"task_id": "qwen-task", "status": "queued"})))
    page.goto(f"{URL}/videos/1")
    page.get_by_role("button", name="换模型重转写").click()
    page.get_by_role("combobox", name="识别引擎").select_option("qwen3-asr")
    expect(page.get_by_role("combobox", name="模型")).to_have_value("Qwen/Qwen3-ASR-1.7B")
    page.get_by_role("button", name="开始重转写").click()
    expect(page).to_have_url(f"{URL}/tasks/qwen-task")
    assert submitted[0]["model"] == "Qwen/Qwen3-ASR-1.7B"


def test_old_qwen_transcript_retranscribes_with_available_model(page):
    from playwright.sync_api import expect

    page.route("**/api/videos/1/document*", lambda route: route.fulfill(json={
        **DOCUMENT, "engine": "qwen3-asr", "model": "Qwen/Qwen3-ASR-0.6B",
        "has_timestamps": False, "segments": [],
    }))
    submitted = []

    def create(route):
        submitted.append(route.request.post_data_json)
        route.fulfill(json={"task_id": "qwen-new", "status": "queued"})

    page.route("**/api/videos/1/retranscribe", create)
    page.goto(f"{URL}/videos/1")
    page.get_by_role("button", name="换模型重转写").click()
    page.get_by_role("button", name="开始重转写").click()
    expect(page).to_have_url(f"{URL}/tasks/qwen-new")
    assert submitted[0]["model"] == "Qwen/Qwen3-ASR-1.7B"


@pytest.mark.parametrize("viewport_width", [390, 1200])
def test_bilibili_selection_sends_only_confirmed_episodes_in_catalog_order(page, viewport_width):
    from playwright.sync_api import expect

    page.set_viewport_size({"width": viewport_width, "height": 844})
    source = "https://www.bilibili.com/video/BV19r4y1w7Nx/"
    items = [{"source": f"{source}?p={i}", "title": f"第{i}讲示例", "duration": 80 if i != 2 else None,
              "selected": i == 2} for i in range(1, 5)]
    page.route("**/api/bilibili/catalog", lambda route: route.fulfill(json={
        "kind": "parts", "title": "课程分集", "items": items,
    }))
    tasks = []
    page.on("request", lambda r: tasks.append(r) if r.method == "POST" and "/api/tasks/" in r.url else None)
    page.goto(URL)
    page.get_by_label("粘贴链接").fill(f"{source}?p=2")
    dialog = page.get_by_role("dialog", name="选择转写集数")
    expect(dialog.get_by_role("checkbox", name=re.compile("^P2 "))).to_be_checked()
    dialog.get_by_role("button", name="全选").click()
    dialog.get_by_role("button", name="清空选择").click()
    expect(dialog.get_by_role("button", name="返回设置")).to_be_disabled()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    page.get_by_role("button", name="查看选集").click()
    expect(dialog.get_by_role("checkbox", name=re.compile("^P2 "))).to_be_checked()
    dialog.get_by_role("checkbox", name=re.compile("^P2 ")).uncheck()
    for part in (4, 1):
        dialog.get_by_role("checkbox", name=re.compile(f"^P{part} ")).check()
    dialog.get_by_role("button", name="返回设置").click()
    page.get_by_role("button", name="修改选集").click()
    dialog.get_by_role("checkbox", name=re.compile("^P3 ")).check()
    dialog.get_by_role("button", name="取消").click()
    page.get_by_role("checkbox", name="为文字稿配图").check()
    assert not tasks
    page.get_by_role("button", name="转写所选 2 集").click()
    expect(page).to_have_url(f"{URL}/tasks")
    assert len(tasks) == 1 and tasks[0].url.endswith("/api/tasks/batch")
    assert tasks[0].post_data_json == {"sources": [items[0]["source"], items[3]["source"]],
                                       "provider": "faster-whisper", "model": "small", "prompt": "",
                                       "include_screenshots": True, "ordered": True}


def test_catalog_failure_allows_retry_or_transcription_of_original_link(page):
    from playwright.sync_api import expect

    attempts = []
    def catalog(route):
        attempts.append(route.request.post_data_json["source"])
        route.fulfill(status=502, json={"detail": "平台繁忙"})
    page.route("**/api/bilibili/catalog", catalog)
    page.goto(URL)
    source = "来自 B 站 https://b23.tv/demo"
    page.get_by_label("粘贴链接").fill(source)
    expect(page.get_by_text("目录读取失败", exact=False)).to_contain_text("可直接转写当前链接")
    with page.expect_request("**/api/bilibili/catalog"):
        page.get_by_role("button", name="重新读取").click()
    expect(page.get_by_role("button", name="重新读取")).to_be_visible()
    with page.expect_request("**/api/tasks/transcribe") as action:
        page.get_by_role("button", name="转成文字").click()
    assert action.value.post_data_json["source"] == source
    assert attempts == ["https://b23.tv/demo", "https://b23.tv/demo"]


def test_old_catalog_response_does_not_reopen_picker_on_multiline_input(page):
    from playwright.sync_api import expect

    held = []
    page.route("**/api/bilibili/catalog", lambda route: held.append(route))
    page.goto(URL)
    source = page.get_by_label("粘贴链接")
    with page.expect_request("**/api/bilibili/catalog"):
        source.fill("BV19r4y1w7Nx")
    source.fill("BV19r4y1w7Nx\nBV1xx411c7XD")
    assert len(held) == 1
    held[0].fulfill(json={"kind": "parts", "title": "已替换的链接", "items": [
        {"source": "BV19r4y1w7Nx", "title": "旧分集", "duration": 109, "selected": True},
    ]})
    with page.expect_request("**/api/tasks/batch") as action:
        page.get_by_role("button", name="转成文字").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    assert action.value.post_data_json["sources"] == ["BV19r4y1w7Nx", "BV1xx411c7XD"]
    assert "ordered" not in action.value.post_data_json


def test_season_picker_submits_one_ordered_batch_directly_from_dialog(page):
    from playwright.sync_api import expect

    sources = [f"https://www.bilibili.com/video/{bv}/" for bv in ("BV19r4y1w7Nx", "BV1xx411c7XD")]
    page.route("**/api/bilibili/catalog", lambda route: route.fulfill(json={
        "kind": "season", "title": "多投稿合集", "items": [
            {"source": s, "title": title, "duration": 88, "selected": i == 1}
            for i, (s, title) in enumerate(zip(sources, ["入门", "进阶"]))
        ],
    }))
    page.goto(URL)
    page.get_by_label("粘贴链接").fill(sources[1])
    dialog = page.get_by_role("dialog", name="选择转写集数")
    expect(dialog.get_by_role("checkbox", name=re.compile("进阶"))).to_be_checked()
    dialog.get_by_role("button", name="全选").click()
    dialog.get_by_role("checkbox", name="为文字稿配图").check()
    with page.expect_request("**/api/tasks/batch") as action:
        dialog.get_by_role("button", name="转写所选 2 集").click()
    expect(page).to_have_url(f"{URL}/tasks")
    assert action.value.post_data_json["sources"] == sources
    assert action.value.post_data_json["include_screenshots"] and action.value.post_data_json["ordered"]


def test_invalid_explicit_page_must_be_corrected_before_submitting(page):
    from playwright.sync_api import expect

    def preview(route):
        if "?p=oops" in route.request.post_data_json["source"]:
            route.fulfill(status=400, json={"detail": "p 必须是正整数"})
        else:
            route.fulfill(json={"kind": "single", "title": "课程", "items": []})
    page.route("**/api/bilibili/catalog", preview)
    page.goto(URL)
    source = page.get_by_label("粘贴链接")
    source.fill("https://www.bilibili.com/video/BV19r4y1w7Nx/?p=oops")
    expect(page.get_by_role("status")).to_contain_text("p 必须是正整数")
    expect(page.get_by_role("button", name="转成文字")).to_be_disabled()
    source.fill("https://www.bilibili.com/video/BV19r4y1w7Nx/?p=1")
    expect(page.get_by_role("button", name="转成文字")).to_be_enabled()
