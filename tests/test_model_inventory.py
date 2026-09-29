from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from v2t.model_inventory import ModelInventory, inspect_model, huggingface_cache_root
from v2t.user_config import AppConfig


def write_model(folder, files=('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.json')):
    folder.mkdir(parents=True, exist_ok=True)
    for name in files:
        (folder / name).write_text('data')


def make_hf_model(root, repo='Systran/faster-whisper-small'):
    repository = root / ('models--' + repo.replace('/', '--'))
    (repository / 'refs').mkdir(parents=True, exist_ok=True)
    (repository / 'refs/main').write_text('a' * 40)
    snapshot = repository / 'snapshots' / ('a' * 40)
    write_model(snapshot)
    return snapshot


def test_hf_root_precedence(monkeypatch, tmp_path):
    for key in ('HF_HUB_CACHE', 'HUGGINGFACE_HUB_CACHE', 'HF_HOME', 'XDG_CACHE_HOME'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg'))
    assert huggingface_cache_root() == tmp_path / 'xdg/huggingface/hub'
    monkeypatch.setenv('HF_HOME', str(tmp_path / 'hf'))
    assert huggingface_cache_root() == tmp_path / 'hf/hub'
    monkeypatch.setenv('HUGGINGFACE_HUB_CACHE', str(tmp_path / 'legacy'))
    assert huggingface_cache_root() == tmp_path / 'legacy'
    monkeypatch.setenv('HF_HUB_CACHE', str(tmp_path / 'current'))
    assert huggingface_cache_root() == tmp_path / 'current'


def test_faster_whisper_snapshot_and_custom_download_root(tmp_path, monkeypatch):
    monkeypatch.setenv('HF_HUB_CACHE', str(tmp_path / 'unused'))
    config = AppConfig()
    config.faster_whisper.download_root = str(tmp_path / 'actual')
    assert inspect_model(config, 'faster-whisper', 'small')['cache_status'] == 'missing'
    snapshot = make_hf_model(tmp_path / 'actual')
    assert inspect_model(config, 'faster-whisper', 'small')['cache_status'] == 'found'
    (snapshot / 'model.bin').write_text('')
    assert inspect_model(config, 'faster-whisper', 'small')['cache_status'] == 'incomplete'
    (snapshot / 'model.bin').unlink()
    (snapshot / 'model.bin').symlink_to(tmp_path / 'absent')
    assert inspect_model(config, 'faster-whisper', 'small')['cache_status'] == 'incomplete'


def test_hf_does_not_pick_unreferenced_snapshot(tmp_path):
    config = AppConfig()
    config.faster_whisper.download_root = str(tmp_path)
    snapshot = make_hf_model(tmp_path)
    (snapshot.parents[1] / 'refs/main').unlink()
    assert inspect_model(config, 'faster-whisper', 'small')['cache_status'] != 'found'


def test_custom_local_whisper_and_sensevoice(tmp_path, monkeypatch):
    config = AppConfig()
    local = tmp_path / 'custom'
    write_model(local)
    assert inspect_model(config, 'faster-whisper', str(local))['cache_status'] == 'found'
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg'))
    whisper = tmp_path / 'xdg/whisper/small.pt'
    whisper.parent.mkdir(parents=True)
    whisper.write_text('weights')
    assert inspect_model(config, 'whisper', 'small')['cache_status'] == 'found'
    assert inspect_model(config, 'whisper', str(whisper))['cache_status'] == 'found'
    sense = tmp_path / 'sensevoice'
    assert inspect_model(config, 'sensevoice', str(sense))['cache_status'] == 'missing'
    write_model(sense, ('model.onnx', 'config.yaml', 'am.mvn', 'chn_jpn_yue_eng_ko_spectok.bpe.model'))
    assert inspect_model(config, 'sensevoice', str(sense))['cache_status'] == 'found'
    assert inspect_model(config, 'volcengine', 'bigmodel')['cache_status'] == 'not_applicable'


def test_scan_never_imports_engines_or_uses_network_and_refreshes(tmp_path, monkeypatch):
    import socket
    config = AppConfig(default_model='small')
    config.faster_whisper.download_root = str(tmp_path)
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        assert name.split('.')[0] not in {'faster_whisper', 'whisper', 'torch', 'ctranslate2', 'funasr_onnx'}
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    monkeypatch.setattr(socket, 'create_connection', lambda *args, **kwargs: pytest.fail('scan accessed network'))
    inventory = ModelInventory(config)
    def small(data):
        return next(m for g in data['items'] if g['provider'] == 'faster-whisper' for m in g['models'] if m['id'] == 'small')
    assert small(inventory.snapshot())['cache_status'] == 'missing'
    make_hf_model(tmp_path)
    assert small(inventory.snapshot())['cache_status'] == 'missing'
    assert small(inventory.snapshot(refresh=True))['cache_status'] == 'found'


def test_unreadable_cache_reports_unknown(tmp_path, monkeypatch):
    config = AppConfig()
    config.faster_whisper.download_root = str(tmp_path)
    original_stat = Path.stat
    def stat(path, *args, **kwargs):
        if 'models--' in str(path):
            raise PermissionError('no permission')
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)
    assert inspect_model(config, 'faster-whisper', 'small')['cache_status'] == 'unknown'
