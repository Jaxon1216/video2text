# 路线图

状态：`待办` / `进行中` / `完成`。阶段 0~5 完成即 V1（tag `v0.1.0`）。

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 0 | 文档地基：AGENTS.md、architecture、roadmap、decisions | 完成 |
| 1 | 抖音解析：分享文本识别、Playwright 截获、下载音频、测试与文档 | 完成 |
| 2 | 减法与全面改名：删 Tk 窗口 / archive / 原作者素材；b2t -> v2t，bili2text -> video2text | 完成 |
| 3 | 时间戳 segments 与导出（带时间戳 TXT / Markdown / SRT，导出 API，CLI `--format`） | 完成 |
| 4 | faster-whisper 引擎 + transcriber 缓存 + 任务并发可配置 | 完成 |
| 5 | React SPA（Copy for AI），跑通后删除 Jinja 页面 | 完成 |
| 6 | B站字幕优先（拿不到再 ASR）+ b23.tv 短链 | 完成 |
| 7 | ASR 评测脚本：CER、术语召回、RTF、成本 | 完成（工具就绪，正式样本待准备） |
| 8 | Docker（可选） | 完成 |

## 0.2.0（进行中）

2026-09-28 开始分阶段实施。和 `CHANGELOG.md` 的「计划 0.2.0」同一套分类。改动前以这里为准。

### 界面

1. **导航和页面层级（完成）。** 独立的新建转写、任务、文字稿入口；保留详情 URL，批量提交进入任务列表，完成后跳转文字稿并提示保存成功。
2. **模型改为选择栏，启动时扫描本机已有的。** CLI 向导已有档位列表（`bootstrap.py` 的 `FASTER_WHISPER_MODELS`：tiny / base / small / medium / large-v3-turbo / large-v3；openai-whisper 另有一套）。Web 首页引擎是 `<select>`，模型仍是手填（`web/src/pages/HomePage.tsx`）。选择栏按下面「识别」里的分层来，不要再把 tiny / base / small 当主路径；云端模型不要套本地档位。档位和 CLI 共用一份。哪些已经在本机，用启动扫描的结果：已有的可选，没有的标出来。现在模型是第一次转写才加载（`FasterWhisperTranscriber._ensure_model`），本机没有就当场下载；`doctor` 只检查依赖是否安装，不看模型文件在不在。扫描只看磁盘，不要把模型加载进内存。云端引擎不用扫。配置了 `download_root` 时扫那个目录，否则扫各引擎的默认缓存位置。
3. **下载前先确认。** 文字稿页的 TXT / Markdown / SRT 是直接链到 `GET /api/videos/{id}/export`（`web/src/pages/VideoPage.tsx`），一点就开始下载。改为先弹窗确认，再下载。
4. **历史标题悬停复制原链接。** 首页「文字稿」列表的标题现在是进文字稿页的链接（`web/src/pages/HomePage.tsx`）。鼠标放在已处理条目的标题上，应能复制原视频链接。列表数据里已有 `source_url`；本地文件没有链接时不要出现复制。

### 任务与进度

5. **任务启动后可以中断。** `TaskStatus` 已有 `cancelled`，但 `TaskService` 没有取消方法，`web.py` 也没有对应接口；首页「处理中」和任务页只能等它跑完。排队中的应直接标取消；进行中的要能停掉当前阶段（下载、抽音频、识别）。涉及 `src/v2t/tasks.py`、`src/v2t/web.py`、`web/src/pages/TaskPage.tsx`、首页任务列表。
6. **轮询反馈与日志降噪（完成）。** 断连保留进度并重试，404 与处理失败分开展示；任务列表保留失败记录。只过滤正常轮询 access log，保留阶段、失败与启动日志。

### 识别

7. **模型分层。** 现在默认是 faster-whisper `small`（2022 年的小模型，CPU 上还会落到 int8），中文和专业词容易差。目标分层：
   - 高质量：Qwen3-ASR-0.6B / 1.7B（新引擎，仓库里还没有）
   - 兼容 / 兜底：faster-whisper `large-v3-turbo`
   - 云端：火山引擎，以后可以加其他 API
   选择栏和默认配置按这三层走。Qwen3-ASR 按「新增 ASR」接入：`transcribers/` 实现、`factory.py` 与 `user_config.py` 注册。
8. **同一链接换模型时复用已有音频。** 现在每次任务都重新下载再 ffmpeg 抽 16k wav（`pipeline.py`）；wav 落在 `.v2t/audio/{标题stem}.wav`，metadata 里有 `audio_path`，但下次同一链接不会去查。期望：音频已经拿到之后，用另一个模型再识别应走缓存，跳过下载和抽音频。缓存键用视频身份（B站 BV、抖音视频 id），不要用标题 stem（会撞名、标题一变就失效）。B站默认字幕优先，命中字幕会跳过下载和 ASR；用户明确要换模型再识别时，应走缓存音频，不要再次被字幕短路。

## 已知问题

- openai-whisper 转写中文时经常输出繁体、没有标点；已改用 faster-whisper 为默认引擎，并在 `language=zh` 时自动加简体中文提示词。openai-whisper 保持原样。
- 专业术语（如“线程池”）容易识别错，通过 `--prompt` 传入术语可明显改善；阶段 7 评测时量化。

## V1 验收流程

粘贴 B站 / 抖音链接 → 看到处理进度 → 拿到带时间戳的文字稿 → 点 Copy for AI → 粘贴给 ChatGPT / Claude 继续提问。
