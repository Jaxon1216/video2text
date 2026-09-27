# AGENTS.md

给 AI 编码助手看的项目说明。开始任何改动前先读完本文件，再按需读 `docs/`。

## 项目定位

video2text：把 B站 / 抖音视频链接快速转成**带时间戳的文字稿**，方便直接复制给 ChatGPT / Claude 继续提问。
个人自用项目，基于开源项目 bili2text 二次开发，不再回馈 upstream。优先"能用、简单"，不追求商业级完备。

## 技术栈

- Python 3.10–3.12（本机 `.venv` 为 3.11），包管理 `uv`，按 extras 安装可选依赖
- CLI：typer；Web：FastAPI（+ 过渡期的 Jinja 模板）；存储：SQLite + 本地文件
- 下载：yt-dlp（B站）、Playwright 浏览器截获（抖音）；音频：ffmpeg
- ASR：openai-whisper / SenseVoice（本地）、火山引擎（云端）

## 常用命令

本机 `uv` 只装在 pyenv 3.11.15 下，执行前先 `source ~/.zshrc`。

```bash
uv sync --extra whisper --extra web --extra douyin   # 安装依赖（按需组合 extras）
uv run playwright install chromium                     # 抖音解析需要的浏览器（仅首次）
uv run pytest -q                                       # 跑测试（也可 .venv/bin/python -m pytest -q）
uv run bili2text tx "<B站链接 / 抖音分享文本 / 本地文件>"
uv run bili2text ui                                    # 启动 Web，默认 http://127.0.0.1:8000
uv run bili2text doctor                                # 检查依赖
```

## 目录地图

```text
src/b2t/
  cli.py            CLI 入口（transcribe/batch/web/server/doctor/bootstrap/language）
  inputs.py         输入解析：本地文件 / B站 / 抖音分享文本 -> SourceRef
  models.py         数据类：SourceRef、DownloadResult、TranscriptResult、TaskRecord...
  factory.py        按 provider 组装 Transcriber，按 source.kind 选择 Downloader
  pipeline.py       核心流程：下载 -> ffmpeg 抽 16k wav -> 转写 -> 写 txt + metadata json
  downloaders/      ytdlp.py（B站）、douyin.py（抖音）
  transcribers/     whisper_local.py、sensevoice_local.py、volcengine.py
  tasks.py          线程池任务服务 + 进度回调
  progress.py       阶段进度（stage -> 总进度区间）
  library.py        把结果登记进视频库、管理转写稿版本
  database.py       SQLite：tasks / task_progress_events / videos / transcript_versions / tags / categories
  web.py            FastAPI：HTML 页面 + /api/*
  user_config.py    .b2t/config.json 读写
  bootstrap.py      首次运行配置向导
  i18n.py           中英文文案
tests/              pytest，网络全部 mock
docs/               架构、路线图、决策、平台说明、API
```

## 核心约定

- **分层边界**：Downloader 只负责"拿到媒体文件 + 元数据"；Transcriber 只负责"音频 -> 文本/segments"；流程编排只在 `pipeline.py`；CLI / Web 只是外壳，不复制业务逻辑。
- **新增平台**：在 `models.SourceKind` 加类型 → `inputs.py` 识别 → `downloaders/` 新建实现 → `factory.py` 注册。不要在 pipeline 里写平台特判。
- **新增 ASR**：`transcribers/` 新建实现 → `factory.py` + `user_config.py` 注册 → `bootstrap.py` / `doctor` 接入。
- **测试不访问网络**：下载器、云 ASR、浏览器一律 mock；真实链接只用于手动端到端验证。
- **保持简单**：不引入 Redis、消息队列、复杂数据库、登录权限等；SQLite + 线程池足够。确需引入必须先在 `docs/decisions.md` 记录理由。
- **抖音**：不逆向 `a_bogus` 等签名算法；走 Playwright 浏览器截获（见 `docs/platforms/douyin.md`）。
- 代码风格跟随现有代码：`from __future__ import annotations`、dataclass、类型标注、少量必要注释。

## 文档同步规则

改了行为、命令、目录结构、配置项或 API，就同步更新：

- 本文件（命令、目录地图、约定）
- `docs/architecture.md`（模块与数据流）
- `docs/roadmap.md`（阶段状态）
- 重要取舍追加到 `docs/decisions.md`
- 平台相关写进 `docs/platforms/*.md`，接口写进 `docs/api.md`

## 提交规范

- Conventional Commits：`feat:` / `fix:` / `refactor:` / `docs:` / `test:` / `chore:`
- 一个功能点一个 commit；改结构和改行为尽量拆开；提交前跑 `uv run pytest -q`
- 在短分支开发，测试通过后合回 `main`
