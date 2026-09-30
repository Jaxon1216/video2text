from __future__ import annotations

import ast
import importlib.util
import os
import re
import stat
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from v2t.model_catalog import FASTER_WHISPER_MODELS, WHISPER_MODELS, QWEN3_ASR_MODEL, model_choices, resolve_model
from v2t.user_config import ALL_PROVIDERS, AppConfig

# Used only when the optional engine is absent. Installed engine aliases take precedence.
_FASTER_REPOS = {name: f"Systran/faster-whisper-{name}" for name in FASTER_WHISPER_MODELS}
_FASTER_REPOS["large-v3-turbo"] = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
_MODULES = {"faster-whisper": "faster_whisper", "whisper": "whisper", "qwen3-asr": "qwen_asr",
            "sensevoice": "funasr_onnx", "volcengine": "requests"}


def huggingface_cache_root() -> Path:
    home = os.getenv("HF_HOME") or str(Path(os.getenv("XDG_CACHE_HOME") or Path.home() / ".cache") / "huggingface")
    root = os.getenv("HF_HUB_CACHE") or os.getenv("HUGGINGFACE_HUB_CACHE") or str(Path(home) / "hub")
    return Path(os.path.expandvars(root)).expanduser()


def _engine_aliases(package: str, source: str, fallback: dict[str, str]) -> dict[str, str]:
    """Read the installed engine's literal mapping without importing its ML runtime."""
    try:
        file = distribution(package).locate_file(source)
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_MODELS" for target in node.targets):
                values = ast.literal_eval(node.value)
                if isinstance(values, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
                    return values
        # An installed but unfamiliar layout should never be reported as confidently cached.
        return {}
    except PackageNotFoundError:
        return fallback
    except (OSError, SyntaxError, ValueError, TypeError):
        return {}


def _nonempty_file(path: Path) -> bool:
    try:
        info = path.stat()
        return stat.S_ISREG(info.st_mode) and info.st_size > 0
    except FileNotFoundError:
        return False


def _result(status: str, message: str, downloadable: bool = False) -> dict:
    return {"cache_status": status, "cache_message": message, "downloadable": downloadable}


def _directory_status(path: Path, required: tuple[str, ...], *, downloadable: bool = False, vocabulary: bool = False) -> dict:
    if not path.exists() and not path.is_symlink():
        return _result("missing", "未找到本地模型文件", downloadable)
    missing = [name for name in required if not _nonempty_file(path / name)] if path.is_dir() else list(required)
    if vocabulary and not any(_nonempty_file(path / name) for name in ("vocabulary.json", "vocabulary.txt")):
        missing.append("vocabulary.json / vocabulary.txt")
    if missing:
        return _result("incomplete", "缺失或为空：" + "、".join(missing), downloadable)
    return _result("found", "关键文件已找到，尚未加载验证", downloadable)


def inspect_model(config: AppConfig, provider: str, model: str) -> dict:
    """Inspect names, sizes and cache refs only; never download, hash or load weights."""
    try:
        if provider == "volcengine":
            return _result("not_applicable", "云端模型，无需本地文件")
        if provider == "sensevoice":
            if not model:
                return _result("missing", "请先配置 SenseVoice 本地模型目录")
            # Matches FunASR SenseVoiceSmall's non-quantized ONNX loader.
            return _directory_status(Path(model).expanduser(), ("model.onnx", "config.yaml", "am.mvn", "chn_jpn_yue_eng_ko_spectok.bpe.model"))
        if provider == "qwen3-asr":
            if model != QWEN3_ASR_MODEL:
                return _result("unknown", "当前仅支持 Qwen3-ASR 1.7B")
            repository = huggingface_cache_root() / "models--Qwen--Qwen3-ASR-1.7B"
            if not repository.exists():
                return _result("missing", "未下载，首次识别需要下载模型", True)
            ref = repository / "refs" / "main"
            if not _nonempty_file(ref):
                return _result("incomplete", "缓存缺少 main 版本引用", True)
            revision = ref.read_text(encoding="utf-8").strip()
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                return _result("incomplete", "缓存版本引用无效", True)
            snapshot = repository / "snapshots" / revision
            return _directory_status(snapshot, ("config.json", "model.safetensors.index.json",
                                                "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors",
                                                "preprocessor_config.json", "tokenizer_config.json", "vocab.json",
                                                "merges.txt"), downloadable=True)
        if provider == "whisper":
            aliases = _engine_aliases("openai-whisper", "whisper/__init__.py", {name: f"{name if name != 'large' else 'large-v3'}.pt" for name in WHISPER_MODELS})
            if model in aliases:
                root = Path(os.getenv("XDG_CACHE_HOME") or Path.home() / ".cache") / "whisper"
                path = root / Path(urlsplit(aliases[model]).path).name
                downloadable = True
            elif Path(model).is_file() or Path(model).is_absolute() or model.startswith((".", "~")) or model.endswith(".pt"):
                path, downloadable = Path(model), False
            else:
                return _result("unknown", "无法判断此自定义模型的缓存位置")
            if _nonempty_file(path):
                return _result("found", "权重文件已找到，尚未校验内容或加载验证", downloadable)
            return _result("incomplete" if path.exists() or path.is_symlink() else "missing", "权重文件缺失、为空或链接失效", downloadable)
        if provider == "faster-whisper":
            required = ("config.json", "model.bin", "tokenizer.json")
            local = Path(model)
            if local.is_dir() or local.is_absolute() or model.startswith((".", "~")):
                return _directory_status(local, required, vocabulary=True)
            aliases = _engine_aliases("faster-whisper", "faster_whisper/utils.py", _FASTER_REPOS)
            repo = aliases.get(model) or (model if re.fullmatch(r"[\w.-]+/[\w.-]+", model) else None)
            if not repo:
                return _result("unknown", "无法判断此模型在当前引擎版本中的缓存位置")
            root = Path(config.faster_whisper.download_root).expanduser() if config.faster_whisper.download_root else huggingface_cache_root()
            repository = root / ("models--" + repo.replace("/", "--"))
            if not repository.exists():
                return _result("missing", "未下载，首次识别需要下载模型", True)
            ref = repository / "refs" / "main"
            if not _nonempty_file(ref):
                return _result("incomplete", "缓存缺少 main 版本引用", True)
            revision = ref.read_text(encoding="utf-8").strip()
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                return _result("incomplete", "缓存版本引用无效", True)
            snapshot = repository / "snapshots" / revision
            result = _directory_status(snapshot, required, vocabulary=True, downloadable=True)
            if result["cache_status"] == "missing":
                return _result("incomplete", "缓存版本引用指向的文件缺失", True)
            return result
        return _result("unknown", "无法识别的引擎")
    except (OSError, ValueError):
        return _result("unknown", "无法读取模型缓存，请检查目录权限或配置")


def _dependency_installed(provider: str) -> bool:
    try:
        return importlib.util.find_spec(_MODULES[provider]) is not None
    except (ImportError, ValueError):
        return False


class ModelInventory:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._lock = Lock()
        self._snapshot = self._scan()

    def _scan(self) -> dict:
        return {"scanned_at": datetime.now(timezone.utc).isoformat(), "items": [
            {"provider": provider, "default_model": resolve_model(self.config, provider),
             "enabled": provider in self.config.enabled_providers,
             "dependency_installed": _dependency_installed(provider),
             "models": [{**item, **inspect_model(self.config, provider, item["id"])} for item in model_choices(self.config, provider)]}
            for provider in ALL_PROVIDERS
        ]}

    def snapshot(self, *, refresh: bool = False) -> dict:
        with self._lock:
            if refresh:
                self._snapshot = self._scan()
            return self._snapshot
