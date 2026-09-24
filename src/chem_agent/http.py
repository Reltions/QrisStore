"""Shared HTTP session for the public chemistry databases the tools query."""

from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "chem-agent/0.1 (chemistry research assistant)"
TIMEOUT = 30

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _session = s
    return _session


def get_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    resp = session().get(url, params=params, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def post_json(url: str, data: dict[str, Any]) -> Any:
    resp = session().post(url, data=data, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_text(url: str, params: dict[str, Any] | None = None) -> str:
    resp = session().get(url, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text
