from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from v2t.config import Settings
from v2t.downloaders import Downloader, DouyinDownloader, YtDlpDownloader
from v2t.pipeline import V2TPipeline
from v2t.transcribers import LocalWhisperTranscriber
from v2t.transcribers.base import Transcriber
from v2t.user_config import AppConfig

# Local models take seconds to load and hundreds of MB of memory, so tasks share one instance per config.
_TRANSCRIBER_CACHE: dict[tuple[str, ...], Transcriber] = {}
_CACHE_LOCK = threading.Lock()


def build_pipeline(
    *,
    settings: Settings,
    config: AppConfig,
    provider: str | None = None,
    model: str | None = None,
) -> V2TPipeline:
    return V2TPipeline(
        settings=settings,
        downloaders=build_downloaders(),
        transcriber=build_transcriber(config=config, provider=provider, model=model),
    )


def build_transcriber(*, config: AppConfig, provider: str | None = None, model: str | None = None) -> Transcriber:
    selected_provider = (provider or config.default_provider).strip().lower()
    selected_model = (model or config.default_model).strip()

    if selected_provider == "faster-whisper":
        from v2t.transcribers.faster_whisper_local import FasterWhisperTranscriber

        options = config.faster_whisper
        return _cached(
            (selected_provider, selected_model or "small", repr(options)),
            lambda: FasterWhisperTranscriber(
                model=selected_model or "small",
                language=options.language,
                device=options.device,
                compute_type=options.compute_type,
                beam_size=options.beam_size,
                vad_filter=options.vad_filter,
                download_root=Path(options.download_root).expanduser() if options.download_root else None,
            ),
        )
    if selected_provider == "whisper":
        return _cached(
            (selected_provider, selected_model or "small"),
            lambda: LocalWhisperTranscriber(model=selected_model or "small"),
        )
    if selected_provider == "sensevoice":
        from v2t.transcribers.sensevoice_local import SenseVoiceSmallTranscriber

        model_dir_text = selected_model or config.sensevoice.model_dir
        if not model_dir_text:
            raise RuntimeError("SenseVoice provider requires a local model directory. Run `video2text bootstrap` first.")
        return _cached(
            (selected_provider, model_dir_text, repr(config.sensevoice)),
            lambda: SenseVoiceSmallTranscriber(
                model_dir=Path(model_dir_text).expanduser(),
                language=config.sensevoice.language,
                use_itn=config.sensevoice.use_itn,
            ),
        )
    if selected_provider == "volcengine":
        from v2t.transcribers.volcengine import VolcengineFlashTranscriber

        return VolcengineFlashTranscriber(
            api_key=config.volcengine.api_key,
            app_key=config.volcengine.app_key,
            access_key=config.volcengine.access_key,
            resource_id=config.volcengine.resource_id,
            model_name=selected_model or config.volcengine.model_name,
            use_itn=config.volcengine.use_itn,
        )
    raise RuntimeError(f"Unsupported provider: {selected_provider}")


def build_downloaders() -> dict[str, Downloader]:
    return {
        "bilibili": YtDlpDownloader(),
        "douyin": DouyinDownloader(),
    }


def clear_transcriber_cache() -> None:
    with _CACHE_LOCK:
        _TRANSCRIBER_CACHE.clear()


def _cached(key: tuple[str, ...], create: Callable[[], Transcriber]) -> Transcriber:
    with _CACHE_LOCK:
        transcriber = _TRANSCRIBER_CACHE.get(key)
        if transcriber is None:
            transcriber = create()
            _TRANSCRIBER_CACHE[key] = transcriber
        return transcriber
