# 本地开发

## 环境

- Python 3.10–3.12（本机 `.venv` 为 3.11，`.python-version` 为 `3.11`）
- [uv](https://docs.astral.sh/uv/)；本机 `uv` 装在 pyenv 3.11.15 下，执行前先 `source ~/.zshrc`
- ffmpeg / ffprobe 在 PATH 中
- 抖音解析需要本机 Chrome，或 `uv run playwright install chromium`

```bash
uv sync --extra whisper --extra web --extra douyin   # 常用组合
uv sync --extra sensevoice --extra volcengine        # 其他引擎按需追加
```

注意：`uv sync` 会卸载没列出的 extras，每次都要带上完整组合。`video2text bootstrap` 会按配置自动拼好命令（`douyin` 始终包含）。

## 常用命令

```bash
uv run pytest -q                       # 全部测试（网络全部 mock）
uv run video2text doctor               # 依赖检查
uv run video2text tx "<输入>" --workspace /tmp/v2t-dev   # 用临时工作区做端到端验证
uv run video2text ui --port 8765       # Web 调试
```

## 配置

- 工作区默认 `./.v2t`（`V2T_HOME` 或 `--workspace` 覆盖），里面的 `config.json` 保存默认引擎和云 ASR key，已被 `.gitignore` 忽略。
- 首次运行会进入配置向导；非交互环境（如后台服务）会写入默认配置（whisper small）。
- 平台相关的环境变量见 `docs/platforms/`。

## 约定

- 新增平台 / 引擎的步骤、分层边界、测试要求见 `AGENTS.md`。
- 端到端验证用真实链接手动跑，不写进自动化测试。
- 改了行为就同步更新 `AGENTS.md` 和 `docs/`。
