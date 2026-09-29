# video2text

[中文](README.md) · [Changelog](CHANGELOG.md)

Turn **Bilibili / Douyin** video links into **timestamped transcripts** you can paste straight into ChatGPT / Claude to summarize, explain, or ask follow-up questions about a specific part.

```text
copy a video link (or the whole share text) → paste → wait → transcript → Copy for AI
```

A personal project built on top of [bili2text](https://github.com/lanbinleo/bili2text) (MIT).

## Sources

| Source | Input | Implementation |
| --- | --- | --- |
| Bilibili | BV id, video URL, app share text | yt-dlp |
| Douyin | app share text, `v.douyin.com` short link, video URL | Playwright page capture (no login) |
| Local files | mp4 / mkv / mov / mp3 / wav / m4a ... | ffmpeg |

## Engines

| Engine | Type | Notes |
| --- | --- | --- |
| faster-whisper (default) | local | Whisper on CTranslate2, fast on CPU |
| Whisper | local | openai-whisper, offline |
| SenseVoice | local | strong on Mandarin, model download required |
| Volcengine | cloud | flash ASR API, accurate, needs an API key |

## Quick start

Requires Python 3.10–3.12, [uv](https://docs.astral.sh/uv/) and ffmpeg.

```bash
uv sync --extra faster-whisper --extra web --extra douyin
# only if Google Chrome is not installed:
uv run playwright install chromium

uv run video2text tx "https://www.bilibili.com/video/BV1xx411c7XD"
uv run video2text tx "https://v.douyin.com/xxxx/"
(cd web && npm install && npm run build)   # build the web UI once
uv run video2text ui   # http://127.0.0.1:8000
```

Docker: `docker compose up -d --build`, then open http://localhost:8000 (details in [docs/docker.md](docs/docker.md)).

The web UI has separate pages for new transcriptions, task status, and transcript history. New configurations default to faster-whisper `large-v3-turbo`; existing settings are preserved. The model picker shows local cache status and refreshes it without downloading weights.

See [AGENTS.md](AGENTS.md) and [docs/](docs/) for architecture, development and API docs.

## License

MIT
