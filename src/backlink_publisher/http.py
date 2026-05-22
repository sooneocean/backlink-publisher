"""Lightweight HTTP helpers: shared `requests.Session` with sensible defaults.

This module provides a cached `get_session()` and convenience `get`/`post`
wrappers that apply a default timeout. Keep this file minimal so tests can
choose to patch either `requests` or these wrappers as needed.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Default timeout (seconds) used when caller doesn't provide one.
DEFAULT_TIMEOUT = 15


@lru_cache(maxsize=1)
def get_session() -> requests.Session:
    """Return a configured global Session with retry/backoff.

    Cached to reuse TCP connections.
    """
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get(url: str, **kwargs: Any) -> requests.Response:
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    return get_session().get(url, timeout=timeout, **kwargs)


def post(url: str, data: Any = None, json: Any = None, **kwargs: Any) -> requests.Response:
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    return get_session().post(url, data=data, json=json, timeout=timeout, **kwargs)
