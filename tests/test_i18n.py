from v2t.i18n import dependency_sync_guidance, normalize_language, tr


def test_normalize_language_accepts_short_codes() -> None:
    assert normalize_language("zh") == "zh-CN"
    assert normalize_language("en") == "en-US"


def test_translate_falls_back_to_default_language() -> None:
    assert tr("unknown", "web_submit") == "开始"


def test_dependency_sync_guidance_mentions_combined_extras_and_bootstrap() -> None:
    guidance = dependency_sync_guidance("en-US")

    assert "uv sync --extra whisper --extra web" in guidance
    assert "bootstrap --sync-only" in guidance
