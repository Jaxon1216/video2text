# 决策记录

每条记录：背景 / 决定 / 原因。新决策追加在末尾。

## D1 纯自用开发，不回馈 upstream

- 背景：项目 fork 自 [lanbinleo/bili2text](https://github.com/lanbinleo/bili2text)（MIT）。
- 决定：不再给 upstream 提 PR，`main` 是自己的主线；`upstream` remote 只作参考。
- 原因：自用需求（抖音、Copy for AI、React 前端）和原项目定位不同，兼顾 PR 会显著增加拆分成本。

## D2 抖音解析用 Playwright 浏览器截获

- 背景：2026-09 实测，抖音短链与分享页返回 WAF 人机校验页，纯 HTTP 拿不到数据；yt-dlp 抖音提取器长期损坏（yt-dlp#9667）；分享页 `_ROUTER_DATA` 已不含视频信息。真实浏览器可自动通过校验，页面会请求 `/aweme/v1/web/aweme/detail/` 并加载独立音频流；音频地址带 `Referer` 可脱离浏览器下载。
- 决定：用 Playwright 打开视频页，截获详情接口 JSON 拿元数据和音频地址，再用普通 HTTP 下载。不逆向 `a_bogus` 签名。
- 原因：单条公开视频、低频、自用；不依赖签名算法和 Cookie 池，抖音改算法时最不容易坏。代价是每条多几秒、需要安装浏览器。若以后嫌慢，可引入 jiji262/douyin-downloader（MIT）的签名实现作为快速路径。

## D3 保持 SQLite + 线程池

- 决定：不引入 Redis、消息队列、Postgres。
- 原因：本地单用户，任务量小；现有 SQLite + ThreadPoolExecutor 已能提供任务、进度和历史。

## D4 全面改名

- 决定：包名 `b2t` -> `v2t`，命令 `bili2text` -> `video2text`，工作目录 `.b2t` -> `.v2t`，环境变量 `B2T_*` -> `V2T_*`。旧 `.b2t` 数据作为测试数据丢弃，不做迁移。
- 原因：不再同步 upstream，没有保留原名的必要。LICENSE 按 MIT 要求保留原作者版权声明。

## D5 Web 前端改为 React SPA

- 决定：`web/` 下 Vite + React + TypeScript，构建产物由 FastAPI 托管（单进程）。SPA 跑通后删除 Jinja 页面。
- 原因：现有 JSON API 已比较完整；SPA 更方便做 Copy for AI、时间戳跳转等交互。

## D6 默认本地引擎改为 faster-whisper

- 背景：openai-whisper 依赖 torch、体积大；中文输出常为繁体且无标点；每个任务都会重新加载模型。
- 决定：新增 faster-whisper（CTranslate2，CPU 用 int8、开启 VAD）并设为默认；`language=zh` 时自动加简体中文提示词，用户 prompt 追加在后；factory 按配置缓存 transcriber；任务并发默认 1（`V2T_TASK_WORKERS`）。
- 原因：不需要 torch，Docker 镜像更小；实测 5 分钟抖音视频输出简体带标点，加术语 prompt 后专业词识别正确。

## D7 B站字幕优先，ASR 兜底

- 背景：B站很多技术视频有 CC 或 AI 字幕，直接用比 ASR 快得多、通常更准；但字幕需要登录 Cookie。
- 决定：`Downloader` 增加可选的 `fetch_subtitles`，pipeline 先取字幕，拿不到或出错就照常下载 + ASR。Cookie 支持 cookies.txt 与 `V2T_COOKIES_FROM_BROWSER`。
- 原因：不改变没有 Cookie 时的行为，只在条件满足时加速；抖音没有可用字幕，保持 ASR。

## D8 Docker 镜像只依赖一个基础镜像

- 背景：本机网络下 Docker Hub、ghcr.io 拉取超时，`deb.debian.org` 不稳定；PyPI 与 Playwright CDN 可用。
- 决定：前端构建和运行时都基于 `python:3.12-slim`（前端用 apt 装 Node 20）；uv 通过 pip 安装；不写 `# syntax=`；`APT_MIRROR` / `PIP_INDEX_URL` / `PLAYWRIGHT_DOWNLOAD_HOST` 作为可选构建参数；镜像不含 openai-whisper（torch）。
- 原因：网络受限时只要本地有一个基础镜像就能构建；镜像体积可控。

## D9 v0.2.0 先完成体验与现有模型管理

- 决定：本轮拆分新建转写、任务、文字稿页面，完善交互、轮询和模型选择。Qwen 接入、任务中断、音频复用延期。
- 默认策略：新配置默认 faster-whisper large-v3-turbo；已有配置不迁移，旧文件缺字段沿用 small。其他引擎独立解析默认值，显式模型名/路径优先。
- 原因：先降低现有流程的使用成本；新引擎与运行中中断需独立验证，避免扩大这一版本的技术风险。

## D10 模型扫描只报告文件状态

- 决定：启动与手动刷新只读检查缓存引用和关键文件；不下载、不导入 ML 引擎、不加载权重，依赖安装状态单独报告。文件已找到不代表能运行。
- 原因：启动开销可控，避免误触发下载或占用大块内存。缺失的可下载模型沿用首次转写下载流程，暂不做独立下载管理。

## D11 任务中断采用协作式检查

- 决定：保留线程池，排队任务取消 Future，运行任务通过取消令牌在处理检查点停止；不为每个任务启动独立工作进程。
- 原因：保持模型实例复用和现有任务架构。模型或网络库的不可中断调用可能继续运行到返回，但其结果不会登记为文字稿。

## D12 完整音频按来源缓存，重识别独立保存

- 决定：远程来源只缓存经格式校验的完整 16k 单声道 PCM WAV；B站按 BV 与分 P、抖音按视频 ID 区分，并记录原链接别名。写入使用临时文件和原子替换；旧文字稿音频在核对来源后导入。换模型重识别强制 ASR，结果生成独立文字稿。
- 原因：模型切换不必重复下载和抽音频；不同模型结果可直接对照，同时保持已有字幕优先流程和编辑版本结构。

## D13 Qwen3-ASR 先接 0.6B 纯文本

- 决定：使用官方 `qwen-asr==0.0.6`，仅开放 `Qwen/Qwen3-ASR-0.6B`；优先 Apple MPS，遇到 MPS 不兼容再试 CPU。术语提示传 `context`，中文模式输出纯文本和空 `segments`；没有时间戳时禁用 SRT。1.7B 与 ForcedAligner 暂不接入。
- 原因：M1 16 GB 本机实测可完成真实中文视频音频识别，进程峰值内存约 2.33 GB。当前 pyenv 的 Python 缺 `_lzma`，直接让官方包读音频路径会报错；改为用标准库/ffmpeg 解码并把 16k 样本数组传给官方接口。
