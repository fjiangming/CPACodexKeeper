"""
轻量级 CPA Management API 客户端。
不 import src/ 的任何模块，完全独立实现。
"""

import asyncio

import httpx


class CPAApi:
    """直接调用 CPA Management API 获取 Token 列表和详情。"""

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        proxy: str | None = None,
        timeout: int = 30,
        max_retries: int = 2,
    ):
        self.base_url = endpoint.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        proxies = proxy if proxy else None
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            proxy=proxies,
            timeout=timeout,
        )

    async def close(self):
        await self.client.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.request(method, url, **kwargs)
                if response.status_code >= 500 and attempt < self.max_retries:
                    await asyncio.sleep(1)
                    continue
                return response
            except (httpx.HTTPError, TimeoutError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(1)
                    continue
                raise
        raise last_error or RuntimeError("request failed")

    async def list_tokens(self) -> list[dict]:
        """获取全部 auth file 列表，过滤出 type=codex 的项。"""
        try:
            resp = await self._request("GET", f"{self.base_url}/v0/management/auth-files")
            if resp.status_code != 200:
                return []
            data = resp.json()
            files = data.get("files", [])
            return [f for f in files if f.get("type") == "codex"]
        except Exception:
            return []

    async def get_token_detail(self, name: str) -> dict | None:
        """获取单个 token 的详细数据。"""
        try:
            resp = await self._request(
                "GET",
                f"{self.base_url}/v0/management/auth-files/download",
                params={"name": name},
            )
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None
