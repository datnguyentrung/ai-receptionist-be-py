"""Trace logging utilities for development and debugging."""

import json
import logging
from pprint import pprint as _py_pprint
from typing import Any

logger = logging.getLogger(__name__)


def pprint(*args: Any, **kwargs: Any) -> None:
    _py_pprint(*args, **kwargs)


def trace_pprint(title: str, data: Any = None) -> None:
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(title)
        if data is not None:
            if isinstance(data, (dict, list)):
                try:
                    logger.debug(json.dumps(data, indent=2, ensure_ascii=False, default=str))
                except Exception:
                    _py_pprint(data)
            else:
                logger.debug(str(data))
