from __future__ import annotations

from typing import Optional

import requests


class HttpClient:
    def __init__(self, http_proxy: Optional[str] = None, timeout: int = 10):
        self.session = requests.Session()
        self.timeout = timeout
        if http_proxy:
            self.session.proxies.update({
                "http": http_proxy,
                "https": http_proxy,
            })

    def get(self, url: str, params: Optional[dict] = None) -> dict:
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def post(self, url: str, json: Optional[dict] = None) -> dict:
        resp = self.session.post(url, json=json, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
