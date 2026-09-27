from v2t.config import Settings
from v2t.downloaders.ytdlp import YtDlpDownloader
from v2t.models import SourceRef


def test_ytdlp_options_keep_single_video_output_template_by_default(tmp_path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["noplaylist"] is True
    assert "playlist_items" not in opts
    assert opts["outtmpl"] == str(settings.downloads_dir / "%(id)s.%(ext)s")


def test_ytdlp_options_select_playlist_item_when_page_is_set(tmp_path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    source = SourceRef(
        raw_input="https://www.bilibili.com/video/BV1xx411c7XD?p=2",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD?p=2",
        page=2,
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["noplaylist"] is False
    assert opts["playlist_items"] == "2"
    assert opts["outtmpl"] == str(settings.downloads_dir / "%(id)s.%(playlist_index)02d.%(ext)s")


def test_ytdlp_options_bypass_proxy_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("V2T_USE_PROXY", raising=False)
    settings = Settings.from_workspace(tmp_path / ".v2t")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["proxy"] == ""


def test_ytdlp_options_keep_system_proxy_when_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("V2T_USE_PROXY", "1")
    settings = Settings.from_workspace(tmp_path / ".v2t")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert "proxy" not in opts


def test_ytdlp_options_falsey_proxy_flag_still_bypasses(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("V2T_USE_PROXY", "0")
    settings = Settings.from_workspace(tmp_path / ".v2t")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["proxy"] == ""


def test_ytdlp_options_use_workspace_cookie_file_when_present(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("V2T_COOKIE_FILE", raising=False)
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    cookie_file = settings.workspace_root / "cookies.txt"
    cookie_file.write_text("# placeholder cookie file\n", encoding="utf-8")
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["cookiefile"] == str(cookie_file)


def test_ytdlp_options_env_cookie_file_takes_priority(tmp_path, monkeypatch) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    workspace_cookie_file = settings.workspace_root / "cookies.txt"
    workspace_cookie_file.write_text("# workspace cookie file\n", encoding="utf-8")
    env_cookie_file = tmp_path / "custom-cookies.txt"
    env_cookie_file.write_text("# env cookie file\n", encoding="utf-8")
    monkeypatch.setenv("V2T_COOKIE_FILE", str(env_cookie_file))
    source = SourceRef(
        raw_input="BV1xx411c7XD",
        kind="bilibili",
        display_name="BV1xx411c7XD",
        bv="BV1xx411c7XD",
        url="https://www.bilibili.com/video/BV1xx411c7XD",
    )

    opts = YtDlpDownloader()._build_ydl_opts(source, settings)

    assert opts["cookiefile"] == str(env_cookie_file)
