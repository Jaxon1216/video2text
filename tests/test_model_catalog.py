from __future__ import annotations

from v2t.model_catalog import FASTER_WHISPER_MODELS, WHISPER_MODELS, model_choices, resolve_model
from v2t.user_config import AppConfig


def test_catalog_preserves_engine_specific_choices():
    config = AppConfig()
    assert [item['id'] for item in model_choices(config, 'faster-whisper')] == list(FASTER_WHISPER_MODELS)
    assert [item['id'] for item in model_choices(config, 'whisper')] == list(WHISPER_MODELS)
    assert 'large-v3-turbo' not in WHISPER_MODELS


def test_provider_default_and_explicit_custom_model():
    config = AppConfig(default_provider='faster-whisper', default_model='medium')
    config.sensevoice.model_dir = '/models/sensevoice'
    config.volcengine.model_name = 'cloud-model'
    assert resolve_model(config, 'faster-whisper') == 'medium'
    assert resolve_model(config, 'whisper') == 'small'
    assert resolve_model(config, 'sensevoice') == '/models/sensevoice'
    assert resolve_model(config, 'volcengine') == 'cloud-model'
    assert resolve_model(config, 'faster-whisper', '/models/custom') == '/models/custom'


def test_custom_default_remains_in_catalog():
    config = AppConfig(default_model='/models/custom')
    assert model_choices(config, 'faster-whisper')[-1]['id'] == '/models/custom'
    assert [m['id'] for m in model_choices(config, 'volcengine')] == ['bigmodel']
