# 本地开发

## 环境

- Python 3.10–3.12（本机 `.venv` 为 3.11，`.python-version` 为 `3.11`）
- [uv](https://docs.astral.sh/uv/)；本机 `uv` 装在 pyenv 3.11.15 下，执行前先 `source ~/.zshrc`
- ffmpeg / ffprobe 在 PATH 中
- 抖音解析需要本机 Chrome，或 `uv run playwright install chromium`
- YouTube 基础依赖含 youtube-transcript-api、yt-dlp EJS 与 Deno，由 `uv sync` 安装；真实测试需要网络能访问 YouTube，自动化用 mock（见 `platforms/youtube.md`）。

```bash
uv sync --extra faster-whisper --extra web --extra douyin                 # 常用组合
uv sync --extra faster-whisper --extra web --extra douyin --extra volcengine --extra whisper --extra qwen3-asr   # 全部引擎
```

注意：`uv sync` 会卸载没列出的 extras，每次都要带上完整组合。`video2text bootstrap` 会按配置自动拼好命令（`douyin` 始终包含）。

## 常用命令

```bash
uv run pytest -q                       # 全部测试（网络全部 mock）
uv run video2text doctor               # 依赖检查
uv run video2text tx "<输入>" --workspace /tmp/v2t-dev   # 用临时工作区做端到端验证
uv run video2text ui --port 8765       # Web 调试
```

同一工作区同时只允许一个任务服务。Web 运行时另开 CLI 转写会提示工作区正在使用；可以通过现有 Web 提交，或用 `--workspace` 指定独立工作区。退出服务后可重新启动，无需删除 `.task-service.lock` 文件。

## 前端

```bash
cd web
npm install
npm run dev      # http://localhost:5173，/api 代理到 127.0.0.1:8000（先启动 uv run video2text ui）
npm run build    # 产物在 web/dist，由 FastAPI 托管
```

后端地址不是 8000 时用 `V2T_BACKEND=http://127.0.0.1:8765 npm run dev`。`web/dist` 和 `node_modules` 不进 git。

## 配置

- 工作区默认 `./.v2t`（`V2T_HOME` 或 `--workspace` 覆盖），里面的 `config.json` 保存默认引擎和云 ASR key，已被 `.gitignore` 忽略。
- 首次运行会进入配置向导；非交互环境（如后台服务）会写入默认配置（faster-whisper large-v3-turbo，中文）。
- faster-whisper 的语言、设备、beam_size、VAD 在 `config.json` 的 `faster_whisper` 段调整；模型缓存在 HuggingFace 默认目录（`~/.cache/huggingface`），国内需 `HF_ENDPOINT=https://hf-mirror.com`。
- Qwen3-ASR 使用官方 1.7B 模型（两片权重，约 4.7 GB），优先 Apple MPS，不支持时回退 CPU。模型首次使用下载到 HuggingFace 缓存；旧 pyenv 缺 `_lzma` 也能工作，因为转写器自行解码音频再传样本数组。
- 平台相关的环境变量见 `docs/platforms/`。

## 约定

- 新增平台 / 引擎的步骤、分层边界、测试要求见 `AGENTS.md`。
- 端到端验证用真实链接和独立工作区手动跑，不写进自动化测试。记录日期、代码版本、代理、模型/语言、耗时、输出与失败阶段；区分新下载和缓存复用。自动化通过、真实链路跑通和准确率验收分别报告，保留失败记录。跨平台的轻量案例索引及复测要点见 [真实回归案例](good-cases.md)，YouTube 详细成功与失败记录见 [平台说明](platforms/youtube.md)。
- `tests/test_*.py` 保存自动化代码，fixture 和 `tmp_path` 生成离线数据；`docs/good-cases.md` 保存可进入 Git 的真实链接与结果摘要；`data/`、`.v2t/` 和 `bench/` 中的媒体、完整稿件与原始日志继续忽略。登记一个案例不代表这次已重新跑过真实网络。
- 改了行为就同步更新 `AGENTS.md` 和 `docs/`。
