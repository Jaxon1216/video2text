# AGENTS.md

给 AI 编码助手看的项目说明。开始任何改动前先读完本文件，再按需读 `docs/`。

## 项目定位

video2text：把 B站 / 抖音视频链接快速转成**带时间戳的文字稿**，方便直接复制给 ChatGPT / Claude 继续提问。
个人自用项目，基于开源项目 [bili2text](https://github.com/lanbinleo/bili2text) 二次开发，不再回馈 upstream。优先"能用、简单"，不追求商业级完备。

## 技术栈

- Python 3.10–3.12（本机 `.venv` 为 3.11），包管理 `uv`，按 extras 安装可选依赖
- CLI：typer；后端：FastAPI（纯 JSON API + 托管前端构建产物）；存储：SQLite + 本地文件
- 前端：`web/` 下 Vite + React + TypeScript，无路由库、无 CSS 框架（手写阅读器风格样式），只做中文
- 下载：yt-dlp（B站）、Playwright 浏览器截获（抖音）；音频：ffmpeg
- ASR：faster-whisper（默认）/ openai-whisper / SenseVoice（本地）、火山引擎（云端）

## 常用命令

本机 `uv` 只装在 pyenv 3.11.15 下，执行前先 `source ~/.zshrc`。

```bash
uv sync --extra faster-whisper --extra web --extra douyin   # 安装依赖（按需组合 extras）
uv run playwright install chromium                     # 抖音解析需要的浏览器（仅本机无 Chrome 时）
export HF_ENDPOINT=https://hf-mirror.com                # 国内下载 faster-whisper 模型
uv run pytest -q                                       # 跑测试（也可 .venv/bin/python -m pytest -q）
uv run video2text tx "<B站链接 / 抖音分享文本 / 本地文件>"
(cd web && npm install && npm run build)                # 构建前端（改了 web/ 之后要重新 build）
uv run video2text ui                                    # 启动 Web，默认 http://127.0.0.1:8000
(cd web && npm run dev)                                 # 前端开发：Vite 5173 端口，/api 代理到 8000
uv run video2text doctor                                # 检查依赖
uv run video2text export <视频ID> --format md            # 导出 txt / plain / md / srt
```

## 目录地图

```text
src/v2t/
  cli.py            CLI 入口（transcribe/batch/web/server/doctor/bootstrap/language）
  inputs.py         输入解析：本地文件 / B站 / 抖音分享文本 -> SourceRef
  models.py         数据类：SourceRef、DownloadResult、TranscriptResult、TranscriptDocument、TaskRecord...
  factory.py        按 provider 组装 Transcriber（按配置缓存复用），按 source.kind 选择 Downloader
  pipeline.py       核心流程：下载 -> ffmpeg 抽 16k wav -> 转写 -> 写 txt + metadata json（含 segments）
  segments.py       segment 结构 {start, end, text}（秒）与归一化
  formatters.py     导出渲染：带时间戳 txt / plain / md / srt
  downloaders/      ytdlp.py（B站）、douyin.py（抖音）
  transcribers/     faster_whisper_local.py、whisper_local.py、sensevoice_local.py、volcengine.py
  tasks.py          线程池任务服务 + 进度回调
  progress.py       阶段进度（stage -> 总进度区间）
  library.py        把结果登记进视频库、管理转写稿版本
  database.py       SQLite：tasks / task_progress_events / videos / transcript_versions / tags / categories
  web.py            FastAPI：/api/* + 托管 web/dist（前端路由回退到 index.html）
  user_config.py    .v2t/config.json 读写
  bootstrap.py      首次运行配置向导
  i18n.py           中英文文案
web/                React 前端：src/api.ts（接口类型）、format.ts（时间戳分段、Copy for AI）、pages/
tests/              pytest，网络全部 mock
docs/               架构、路线图、决策、平台说明、API
```

## 核心约定

- **分层边界**：Downloader 只负责"拿到媒体文件 + 元数据"；Transcriber 只负责"音频 -> 文本/segments"；流程编排只在 `pipeline.py`；CLI / Web 只是外壳，不复制业务逻辑。
- **新增平台**：在 `models.SourceKind` 加类型 → `inputs.py` 识别 → `downloaders/` 新建实现 → `factory.py` 注册。不要在 pipeline 里写平台特判。
- **新增 ASR**：`transcribers/` 新建实现，返回的 `segments` 必须用 `normalize_segments` 归一化 → `factory.py` + `user_config.py` 注册 → `bootstrap.py` / `doctor` 接入。
- **测试不访问网络**：下载器、云 ASR、浏览器一律 mock；真实链接只用于手动端到端验证。
- **保持简单**：不引入 Redis、消息队列、复杂数据库、登录权限等；SQLite + 线程池足够。确需引入必须先在 `docs/decisions.md` 记录理由。
- **抖音**：不逆向 `a_bogus` 等签名算法；走 Playwright 浏览器截获（见 `docs/platforms/douyin.md`）。
- **前端**：分段规则（30 秒）必须和 `formatters.py` 保持一致；Copy for AI 的文本格式在 `web/src/format.ts` 的 `buildCopyForAI`。
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
