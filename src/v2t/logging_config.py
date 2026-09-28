from __future__ import annotations

import logging
import re
from copy import deepcopy
from urllib.parse import urlsplit


class PollingAccessFilter(logging.Filter):
    """Keep errors and mutations; suppress only successful list/progress requests."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        _, method, target, _, status = args
        if method != "GET" or not isinstance(status, int) or not 200 <= status < 300:
            return True
        path = urlsplit(str(target)).path.rstrip("/")
        return not bool(re.fullmatch(r"/api/(?:videos|tasks(?:/[^/]+(?:/progress)?)?)", path))


def server_log_config() -> dict:
    from uvicorn.config import LOGGING_CONFIG

    config = deepcopy(LOGGING_CONFIG)
    config["filters"] = {"polling": {"()": PollingAccessFilter}}
    config["handlers"]["access"]["filters"] = ["polling"]
    config["loggers"]["v2t"] = {"handlers": ["default"], "level": "INFO", "propagate": False}
    return config
