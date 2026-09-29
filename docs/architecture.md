# 架构

## 总览

CLI 优先的 Python 包。CLI、Web 只是外壳，全部通过 `TaskService` 提交任务，由统一的 `V2TPipeline` 执行。Web 由 FastAPI（`/api/*`）和 `web/` 下的 React SPA 组成，FastAPI 直接托管 SPA 的构建产物，只需要一个进程。

```mermaid
flowchart LR
    CLI["cli.py"] --> TaskService
    Web["web.py"] --> TaskService
    TaskService["tasks.py 线程池"] --> Factory["factory.build_pipeline"]
    Factory --> Pipeline["pipeline.V2TPipeline"]
    Pipeline --> Inputs["inputs.parse_source"]
    Pipeline --> Downloader["downloaders/*"]
    Pipeline --> FFmpeg["ffmpeg 转 16k 单声道 wav"]
    Pipeline --> Transcriber["transcribers/*"]
    TaskService --> Library["library.py"]
    Library --> Files["本地文件：txt + metadata json"]
    Library --> DB["SQLite 索引"]
```

## 一次转写的数据流

1. `inputs.parse_source(raw)` 把输入解析为 `SourceRef(kind=bilibili|douyin|video|audio, ...)`。
2. 远程来源（bilibili / douyin）交给 `factory` 按 `kind` 选出的 Downloader。若 `prefer_subtitles`（默认开）先调用 `fetch_subtitles`：拿到平台字幕就跳过下载和 ASR（`transcript_source=subtitle`）；否则 `download` 得到 `DownloadResult(video_path, title, metadata)`，`video_path` 可以是视频也可以是纯音频文件。
3. `pipeline._extract_audio` 用 ffmpeg 转成 16kHz 单声道 wav（本地音频文件跳过这一步）。
4. `Transcriber.transcribe(audio_path, prompt, progress)` 返回 `{"text", "segments", "language", "model", ...}`，其中 `segments` 已归一化为 `[{start, end, text}]`（秒，见 `segments.py`）。
5. pipeline 写出 `transcripts/original/<stem>-<时间>.txt`（纯文本）和 `metadata/<stem>-<时间>.json`（来源、下载信息、`transcript_source`、`segments`）。
6. `library.register_transcript_result` 在 SQLite 里登记 video 和当前转写稿版本。
7. 展示与导出时，`library.load_document(video_id)` 组装 `TranscriptDocument`（元数据 + 当前文本 + segments），交给 `formatters.export_document` 渲染成 txt / plain / md / srt。编辑过的版本不带时间戳。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `inputs.py` | 输入识别：本地文件后缀、B站 BV / 链接、抖音分享文本与链接 |
| `models.py` | 纯数据类，无业务逻辑；`TranscriptDocument` 是导出和以后 AI 功能的统一输入 |
| `segments.py` | segment 结构与各引擎输出的归一化 |
| `formatters.py` | 纯函数：时间戳格式化、按 30 秒合并段落、txt / md / srt 渲染 |
| `downloaders/` | `Downloader.download(...) -> DownloadResult`；可选 `fetch_subtitles(...) -> SubtitleResult \| None`（B站实现，抖音返回 None） |
| `transcribers/` | `Transcriber.transcribe(audio_path, prompt, progress) -> dict` |
| `factory.py` | 组装 pipeline：provider -> Transcriber（按 provider + model + 配置缓存，模型只加载一次），kind -> Downloader |
| `pipeline.py` | 唯一的流程编排处 |
| `tasks.py` | `ThreadPoolExecutor`（并发数 `V2T_TASK_WORKERS`，默认 1）；把进度快照写库并通知监听者 |
| `progress.py` | 各阶段占总进度的区间：preparing / downloading / extracting_audio / transcribing / writing_outputs / indexing |
| `library.py` | 登记结果、编辑后另存新版本、启动时扫描工作区补索引 |
| `database.py` | SQLite 表结构与查询 |
| `web.py` | FastAPI：`/api/*`（接口见 `docs/api.md`）+ 托管 `web/dist`，非 API 路径回退到 `index.html`；没构建时返回提示页 |
| `web/`（前端） | React SPA：新建转写 `/`、任务列表 `/tasks`、文字稿列表 `/videos`；保留任务与文字稿详情。单条提交进入任务详情，批量进入任务列表；完成后跳转文字稿并提示保存成功 |
| `user_config.py` / `bootstrap.py` | `config.json` 读写与首次配置向导 |

## 工作区目录

默认 `./.v2t`（可用环境变量 `V2T_HOME` 或 `--workspace` 覆盖）：

```text
.v2t/
  config.json          用户配置（默认 provider/model、云 ASR key）
  app.db               SQLite 索引
  downloads/           下载的视频/音频
  audio/               ffmpeg 输出的 16k wav
  transcripts/original 原始转写稿
  transcripts/edited   Web 上编辑后另存的版本
  metadata/            每次转写的 metadata json（来源、引擎、下载信息、segments）
  exports/             CLI 导出的 txt / md / srt
  browser/             抖音解析用的持久化浏览器目录
```

本地文件是真实数据源，SQLite 只做索引；数据库里存的是相对工作区的路径。

## 数据库表

- `tasks` / `task_progress_events`：任务状态与进度事件
- `videos`：一条转写结果（`source_kind`、来源、标题、引擎、文件路径、当前版本指针）
- `transcript_versions`：`kind=original|edited`，每个版本一个文件
- `categories` / `tags` / `video_tags`：分类与标签

`tasks.kind` 与 `transcript_versions.kind` 是以后扩展"总结 / 分章节"等新任务、新产物的挂载点。

## 配置与环境变量

| 变量 | 作用 |
| --- | --- |
| `V2T_HOME` | 工作区目录 |
| `V2T_LANG` | 界面语言 |
| `V2T_COOKIE_FILE` | B站 cookies.txt 路径（默认 `<工作区>/cookies.txt`） |
| `V2T_USE_PROXY` | B站下载是否走系统代理（默认直连） |
| `V2T_DEFAULT_PROVIDER` / `V2T_DEFAULT_MODEL` / `V2T_VOLCENGINE_API_KEY` / `V2T_PREFER_SUBTITLES` | 运行时覆盖 `config.json`（不写回文件），主要给 Docker 用 |
| `V2T_WEB_DIST` | 前端构建产物目录（默认仓库里的 `web/dist`） |
| `V2T_TASK_WORKERS` | 同时执行的任务数（默认 1；只用云 ASR 时可调大） |
| `HF_ENDPOINT` | HuggingFace 镜像，国内下载 faster-whisper 模型用 `https://hf-mirror.com` |
| `V2T_DOUYIN_HEADLESS` | 抖音解析是否用无头浏览器（默认 `1`） |
| `V2T_DOUYIN_BROWSER` | 抖音解析用哪个浏览器：`auto`（默认，先本机 Chrome 再自带 Chromium）/ `chrome` / `chromium` |

平台细节见 `docs/platforms/`。

## Web 状态与日志

任务列表活跃时每 1.5 秒刷新、空闲时每 6 秒刷新；任务详情每 1 秒刷新，终态或 404 停止。断连保留最后快照，每 6 秒重试；文字稿加载错误提供重试入口。请求超时为 15 秒，页面卸载后不再更新状态或安排轮询。

`logging_config.py` 只过滤成功的任务列表/详情/进度和文字稿列表 GET 日志，异常请求与导出等其他请求保留。`tasks.py` 按任务阶段变化记录日志，失败为 ERROR，不按百分比刷屏。

文字稿详情用原生 `<dialog>` 提供站内导出确认，浏览器负责焦点约束；组件关闭后恢复原按钮焦点，确认后才访问导出接口。

文字稿列表的原链接复制按钮与详情链接独立，支持悬停、键盘与触屏，反馈复制结果；本地文件不显示该操作。

模型选项统一定义在 `model_catalog.py`，CLI 配置向导复用该目录；默认模型解析按引擎隔离，显式模型名或路径优先。

`/api/models` 提供共享模型目录，Web 按引擎切换选项。CLI、Web API、factory 都通过 `resolve_model` 解析默认值，环境变量/CLI 参数只覆盖运行配置。新建配置默认 faster-whisper large-v3-turbo；已有配置保留原值，旧文件缺少 model 字段时仍回退 small。自定义模型名和路径保留。

## 模型文件扫描

`model_inventory.ModelInventory` 在创建 Web app 时扫描一次，普通 `/api/models` 请求复用快照；手动刷新在线程池中执行并串行更新快照。只检查文件大小、关键文件和缓存引用，不访问网络、不校验大文件哈希、不导入 ML 引擎。

- faster-whisper 优先检查 `download_root`，否则按 `HF_HUB_CACHE` → `HUGGINGFACE_HUB_CACHE` → `HF_HOME/hub` → `XDG_CACHE_HOME/huggingface/hub` → `~/.cache/huggingface/hub` 定位。只认可 `refs/main` 指向的 snapshot，检查 config、model、tokenizer、vocabulary；失效软链接不算已找到。
- 模型别名通过静态读取已安装引擎的 `_MODELS` 字典解析，避免导入权重运行库；无法识别的布局返回 unknown。依赖不存在时只用已知档位映射作文件检查，并独立显示依赖未安装。
- Whisper 检查 `XDG_CACHE_HOME/whisper`（默认 `~/.cache/whisper`）或自定义权重文件。SenseVoice 检查配置目录中的 ONNX、配置、归一化与分词文件。火山不扫描本地文件。
- 前端标记缺失/不完整/未知，缺失的可下载模型仍可选并提示首次下载。SenseVoice 缺少文件时禁用提交，提示配置目录；刷新保留当前选择。

SenseVoice 文件规则参考 [FunASR 加载器](https://github.com/modelscope/FunASR/blob/main/runtime/python/onnxruntime/funasr_onnx/sensevoice_bin.py)，与当前包装器的非量化默认参数一致。

任务进入终态时，先原子写入结果 ID / 错误信息与完成时间，再通知进度监听者，保证客户端停止轮询时已经取得完整终态数据。

交互式 bootstrap 显式重配默认引擎后同步其模型值，包括保持同一 SenseVoice / 火山引擎但修改目录或云模型的情况；普通加载配置仍不迁移旧值。
