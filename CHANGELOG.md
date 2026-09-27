# 更新日志

对应 `main` 上的分阶段提交。`v0.1.0` 打在 Web 界面那一次提交上。

## 未发布

阶段 6–8，在 `v0.1.0` 之后。

- B站字幕优先：有 CC / AI 字幕（需登录 Cookie，支持 `V2T_COOKIES_FROM_BROWSER=chrome` 直接读浏览器）时直接使用；没有再走 ASR。`--force-asr` 可强制识别。支持 `b23.tv` 短链。
- 新增 ASR 评测脚本 `scripts/bench_asr.py`：多引擎、有无术语提示词对比，输出 CER、术语召回、RTF、成本。
- Docker：`docker compose up -d --build` 一键运行（前端 + API + faster-whisper + 抖音 Chromium），数据挂载在 `./data`；支持 `APT_MIRROR` 等国内构建参数。配置可用 `V2T_DEFAULT_PROVIDER` / `V2T_DEFAULT_MODEL` / `V2T_VOLCENGINE_API_KEY` / `V2T_PREFER_SUBTITLES` 环境变量覆盖。
- 抖音解析的浏览器 UA 改为跟随实际系统（修复 Linux 容器里详情接口返回空数据）。

## 0.1.0

阶段 0–5。粘贴 B站 / 抖音链接，得到带时间戳的文字稿，再 Copy for AI。

- 新增 `AGENTS.md` 与 `docs/`（架构、路线图、决策记录）。
- 新增抖音支持：粘贴 App 分享文本或链接即可转写（Playwright 浏览器截获，不需要登录）。B站分享文本也可以直接粘贴。
- 移除 Tk 桌面窗口、旧版脚本和原项目素材。
- 项目更名为 video2text：包名 `v2t`、命令 `video2text`、工作目录 `.v2t`、环境变量 `V2T_*`。
- 转写结果保留时间戳（segments），支持导出带时间戳 TXT、Markdown、SRT（Web API 与 `video2text export` / `tx --format`）。
- 新增 faster-whisper 并设为默认：CPU int8、VAD、真实进度；中文自动输出简体和标点；模型按配置缓存，任务并发默认 1（`V2T_TASK_WORKERS`）。
- 全新 Web 界面（React，阅读器风格）：粘贴链接、进度、带时间戳的文字稿；Copy for AI、复制单段、下载 TXT / Markdown / SRT；B站时间戳可跳转原视频。旧的 Jinja 页面已移除。

## 起点

- fork 自 [bili2text](https://github.com/lanbinleo/bili2text) 0.3.0（CLI + Web + 任务与视频库管理，Whisper / SenseVoice / 火山引擎）。
