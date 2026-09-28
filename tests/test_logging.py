from __future__ import annotations

import logging

import pytest

from v2t.logging_config import PollingAccessFilter


@pytest.mark.parametrize(('method', 'path', 'status', 'keep'), [
    ('GET', '/api/tasks', 200, False),
    ('GET', '/api/tasks/demo?poll=1', 200, False),
    ('GET', '/api/tasks/demo/progress', 200, False),
    ('GET', '/api/videos?query=abc', 200, False),
    ('GET', '/api/tasks/demo', 404, True),
    ('GET', '/api/tasks', 500, True),
    ('POST', '/api/tasks/transcribe', 200, True),
    ('GET', '/api/videos/1/export?format=txt', 200, True),
    ('GET', '/api/models', 200, True),
])
def test_filter_only_suppresses_successful_polling(method, path, status, keep):
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s - "%s %s HTTP/%s" %d',
                               ('client', method, path, '1.1', status), None)
    assert PollingAccessFilter().filter(record) is keep


def test_filter_keeps_unstructured_messages():
    record = logging.LogRecord('uvicorn.access', logging.ERROR, '', 0, 'error', (), None)
    assert PollingAccessFilter().filter(record)
