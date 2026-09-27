# video2text

[English](README.en.md) · [更新日志](CHANGELOG.md)

把 **B站 / 抖音** 视频链接快速转成**带时间戳的文字稿**，一键复制给 ChatGPT / Claude，让 AI 帮你总结、解释或针对某一段继续追问。

```text
复制视频链接（或整段分享文本） → 粘贴 → 等待转写 → 拿到文字稿 → Copy for AI
```

个人自用项目，基于 [bili2text](https://github.com/lanbinleo/bili2text)（MIT）二次开发。

## 支持的来源

| 来源 | 输入形式 | 实现 |
| --- | --- | --- |
| B站 | BV 号、视频链接、App 分享文本 | yt-dlp |
| 抖音 | App 分享文本、`v.douyin.com` 短链、视频链接 | Playwright 浏览器截获（不需要登录） |
| 本地文件 | mp4 / mkv / mov / mp3 / wav / m4a 等 | ffmpeg |

## 转写引擎

| 引擎 | 类型 | 说明 |
| --- | --- | --- |
| Whisper | 本地 | openai-whisper，离线运行 |
| SenseVoice | 本地 | 中文效果好，需要先下载模型 |
| 火山引擎 | 云端 | 极速版 API，准确率高，需要 API Key |

## 快速开始

需要 Python 3.10–3.12、[uv](https://docs.astral.sh/uv/) 和 ffmpeg。

```bash
uv sync --extra whisper --extra web --extra douyin
# 本机没有 Chrome 时才需要：
uv run playwright install chromium
```

转写：

```bash
uv run video2text tx "https://www.bilibili.com/video/BV1xx411c7XD"
uv run video2text tx "复制打开抖音，看看【xxx的作品】... https://v.douyin.com/xxxx/"
uv run video2text tx ./my-video.mp4 --provider whisper --model medium
```

批量（每行一个输入）：

```bash
uv run video2text batch --file sources.txt
```

Web 界面：

```bash
uv run video2text ui   # http://127.0.0.1:8000
```

## 命令一览

| 命令 | 缩写 | 说明 |
| --- | --- | --- |
| `video2text transcribe` | `tx` | 转写一个输入 |
| `video2text batch` | - | 批量转写 |
| `video2text web` | `ui` | 启动 Web 界面 |
| `video2text server` | `srv` | 以 `0.0.0.0` 启动服务 |
| `video2text bootstrap` | `init` | 配置向导 |
| `video2text doctor` | `diag` | 检查运行环境 |
| `video2text language` | `lang` | 切换界面语言 |

## 文档

- [AGENTS.md](AGENTS.md)：项目说明（给 AI 编码助手，也适合人读）
- [docs/architecture.md](docs/architecture.md)：架构与数据流
- [docs/development.md](docs/development.md)：本地开发
- [docs/api.md](docs/api.md)：HTTP API
- [docs/platforms/](docs/platforms/)：各平台实现细节
- [docs/roadmap.md](docs/roadmap.md)：路线图

## 使用须知

请遵守所在地区的法律与平台规则，只处理你有权使用的内容。

## 许可证

MIT
