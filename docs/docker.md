# Docker

## 快速开始

```bash
docker compose up -d --build
# 浏览器打开 http://localhost:8000
```

数据都在仓库下的 `./data`（已被 `.gitignore` 忽略）：`config.json`、SQLite、文字稿、下载的音频、`cookies.txt`、模型缓存 `models/`。删掉容器不会丢数据。

## 镜像内容

- 基础镜像 `python:3.12-slim`（Debian 13），前端构建阶段也用它（apt 装 Node 20），整个构建只依赖这一个基础镜像。
- Python 依赖：`faster-whisper`、`web`、`douyin`、`volcengine` 这几个 extras。**不含 openai-whisper**（torch 太大）。
- ffmpeg、Playwright Chromium 及其系统库（抖音解析用）。
- 解压后约 3.5GB；第一次使用未缓存的模型进行语音识别时会下载模型，缓存在 `data/models`；新配置默认 large-v3-turbo。

## 配置（`.env` 或 compose 的 environment）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HF_ENDPOINT` | `https://hf-mirror.com` | 模型下载镜像；海外网络可设为 `https://huggingface.co` |
| `V2T_DEFAULT_PROVIDER` | 空 | 不覆盖已保存的引擎；新配置使用 faster-whisper |
| `V2T_DEFAULT_MODEL` | 空 | 不覆盖已保存的模型；新配置使用 large-v3-turbo，可显式指定其他档位 |
| `V2T_VOLCENGINE_API_KEY` | 空 | 火山引擎 API Key，填了之后 Web 里可以选火山引擎 |
| `V2T_PREFER_SUBTITLES` | `1` | 是否优先用 B站字幕 |

环境变量只在运行时生效，不会写回 `data/config.json`。其他配置（如 faster-whisper 的 beam_size）直接改 `data/config.json`。

B站字幕需要登录 Cookie：把浏览器导出的 Netscape 格式 `cookies.txt` 放到 `data/cookies.txt`。容器里读不到宿主机浏览器，所以 `V2T_COOKIES_FROM_BROWSER` 在 Docker 里不可用。

## 网络受限时构建（中国大陆）

2026-09 在本机实测：Docker Hub、ghcr.io 拉取超时，`deb.debian.org` 下载中途断开，PyPI 和 Playwright 官方 CDN 可以访问。对应处理：

- 只依赖一个基础镜像；本机已有 `python:3.12-slim` 时不需要访问 Docker Hub。没有的话配置 Docker Desktop 的 registry mirror，或用 `--build-arg BASE_IMAGE=<镜像代理地址>/library/python:3.12-slim`。
- 不写 `# syntax=` 指令（它会额外拉 `docker/dockerfile` 镜像）；uv 用 pip 从 PyPI 安装，不从 ghcr.io 拷贝。
- Debian 源用 `APT_MIRROR` 替换：

```bash
APT_MIRROR=https://mirrors.aliyun.com docker compose build
# 或
docker build --build-arg APT_MIRROR=https://mirrors.aliyun.com -t video2text:latest .
```

- 可选 `PIP_INDEX_URL`（uv 安装源）、`PLAYWRIGHT_DOWNLOAD_HOST`（Chromium 下载源；npmmirror 常常缺最新的 linux-arm64 版本，官方 CDN 一般能直接访问）。

## 性能

- macOS 上 Docker 容器用不了 Apple GPU，本地 ASR 走 CPU；faster-whisper 在 CPU 上用 int8，实测与在 Mac 上原生运行接近。
- NVIDIA GPU 机器需要 CUDA 版的 CTranslate2 与 `--gpus all`，当前镜像没有针对 GPU 优化。
- `V2T_TASK_WORKERS` 默认 1；只用云 ASR 时可以调大。

## 抖音在容器里

容器里没有 Chrome，用 Playwright 自带的 Chromium（`V2T_DOUYIN_BROWSER=chromium`）。页面签名会带浏览器指纹，UA 必须和实际系统一致，代码会按平台自动选择 UA（Linux 容器用 Linux UA）。`shm_size: 1gb` 不能去掉，否则 Chromium 容易崩溃。
