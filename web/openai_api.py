"""
轻量级 OpenAI Usage API 检测客户端。
不 import src/ 的任何模块，完全独立实现。
"""

import asyncio

import httpx

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


class OpenAIApi:
    """检测 Token 存活状态和额度使用情况。"""

    def __init__(self, *, proxy: str | None = None, timeout: int = 15, max_retries: int = 2):
        self.max_retries = max(0, max_retries)
        proxies = proxy if proxy else None
        self.client = httpx.AsyncClient(proxy=proxies, timeout=timeout)

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

    async def check_usage(self, access_token: str, account_id: str | None = None) -> dict:
        """
        调用 OpenAI usage 接口，返回解析后的使用信息。

        返回格式:
        {
            "status_code": 200,
            "plan_type": "team",
            "primary_pct": 45,
            "primary_window_seconds": 18000,
            "primary_reset_at": 1714000000,
            "secondary_pct": 30 | None,
            "secondary_window_seconds": 604800 | None,
            "secondary_reset_at": 1714500000 | None,
            "has_credits": False,
            "error": None,
        }
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "codex_cli_rs/0.76.0",
        }
        if account_id:
            headers["Chatgpt-Account-Id"] = account_id

        try:
            resp = await self._request("GET", USAGE_URL, headers=headers)
            result = {
                "status_code": resp.status_code,
                "plan_type": "unknown",
                "primary_pct": 0,
                "primary_window_seconds": None,
                "primary_reset_at": None,
                "secondary_pct": None,
                "secondary_window_seconds": None,
                "secondary_reset_at": None,
                "has_credits": False,
                "error": None,
            }

            if resp.status_code != 200:
                result["error"] = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
                return result

            body = resp.json()
            rate_limit = body.get("rate_limit") or {}
            primary = rate_limit.get("primary_window") or {}
            secondary = rate_limit.get("secondary_window")
            credits = body.get("credits") or {}

            result["plan_type"] = body.get("plan_type", "unknown")
            result["primary_pct"] = int(primary.get("used_percent", 0) or 0)
            result["primary_window_seconds"] = primary.get("limit_window_seconds")
            result["primary_reset_at"] = primary.get("reset_at")
            result["has_credits"] = bool(credits.get("has_credits", False))

            if isinstance(secondary, dict):
                result["secondary_pct"] = int(secondary.get("used_percent", 0) or 0)
                result["secondary_window_seconds"] = secondary.get("limit_window_seconds")
                result["secondary_reset_at"] = secondary.get("reset_at")

            return result
        except Exception as exc:
            return {
                "status_code": None,
                "plan_type": "unknown",
                "primary_pct": 0,
                "primary_window_seconds": None,
                "primary_reset_at": None,
                "secondary_pct": None,
                "secondary_window_seconds": None,
                "secondary_reset_at": None,
                "has_credits": False,
                "error": str(exc),
            }
