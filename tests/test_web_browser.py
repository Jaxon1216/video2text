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
                    {"provider": "qwen3-asr", "default_model": "Qwen/Qwen3-ASR-0.6B", "enabled": False,
                     "models": [{"id": "Qwen/Qwen3-ASR-0.6B", "label": "Qwen3-ASR 0.6B"}]},
                    {"provider": "sensevoice", "default_model": "", "enabled": False, "models": []},
                    {"provider": "volcengine", "default_model": "bigmodel", "enabled": False,
                     "models": [{"id": "bigmodel", "label": "bigmodel"}]},
                ]},
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


def test_cancel_running_task_from_detail(page):
    from playwright.sync_api import expect

    page.route("**/api/tasks/demo/cancel", lambda route: route.fulfill(json={**TASK, "cancel_requested": True}))
    page.goto(f"{URL}/tasks/demo")
    page.get_by_role("button", name="中断任务").click()
    expect(page.get_by_text("正在中断", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="中断任务")).to_have_count(0)


def test_cancel_queued_task_from_list_without_navigation(page):
    from playwright.sync_api import expect

    queued = {**TASK, "status": "queued", "current_stage": "queued", "id": "queued"}
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

    page.route("**/api/videos/1/document", lambda route: route.fulfill(json={**DOCUMENT, "has_timestamps": False, "segments": []}))
    page.goto(f"{URL}/videos/1")
    expect(page.get_by_text("SRT", exact=True)).to_have_attribute("title", "没有时间戳，无法导出字幕")
    expect(page.get_by_role("button", name="SRT", exact=True)).to_have_count(0)


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
    page.get_by_role("combobox", name="识别引擎").select_option("volcengine")
    expect(model).to_have_value("bigmodel")
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
    expect(page.get_by_role("combobox", name="模型")).to_have_value("Qwen/Qwen3-ASR-0.6B")
    page.get_by_role("button", name="开始重转写").click()
    expect(page).to_have_url(f"{URL}/tasks/qwen-task")
    assert submitted[0]["model"] == "Qwen/Qwen3-ASR-0.6B"
