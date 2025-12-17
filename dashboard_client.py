# dashboard_client.py

import os
import time
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.core.star.context import Context
from astrbot.core.star.star import StarMetadata


class DashboardClient:
    """
    面板 HTTP 客户端
    - 复用 aiohttp.ClientSession
    - 自动缓存 & 续期
    """

    # token 有效期阈值（秒）
    TOKEN_VALID_THRESHOLD = 23 * 3600

    def __init__(self, context: Context):
        self.context = context
        self.stars: list[StarMetadata] = context.get_all_stars()
        self.star_manager = self.context._star_manager

        dbc = context.get_config().get("dashboard", {})
        self.host = dbc.get("host", "127.0.0.1")
        self.port = int(os.environ.get("DASHBOARD_PORT", dbc.get("port", 6185)))
        if self.host == "0.0.0.0":
            self.host = "127.0.0.1"

        # 接口地址
        self.login_url = f"http://{self.host}:{self.port}/api/auth/login"
        self.restart_url = f"http://{self.host}:{self.port}/api/stat/restart-core"

        # 缓存用
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_ts: float | None = None

    # -------------------- 生命周期 --------------------
    async def initialize(self):
        self._session = aiohttp.ClientSession()

    async def terminate(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # -------------------- 公共接口 --------------------
    async def restart(self) -> None:
        """重启 AstrBot 核心"""
        await self._request("POST", self.restart_url)

    # -------------------- 内部工具 --------------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None= None,
        **kwargs,
    ) -> dict[str, Any]:
        """统一网络请求：自动带鉴权、自动续期、自动抛异常"""
        if self._session is None:
            raise RuntimeError("请使用 async with DashboardClient() 初始化会话")

        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}"}

        async with self._session.request(
            method, url, headers=headers, json=json, **kwargs
        ) as resp:
            if resp.status == 401:
                # 401 说明 token 失效，强制刷新再试一次
                logger.info("Token 失效，尝试重新登录")
                token = await self._login()
                headers["Authorization"] = f"Bearer {token}"
                async with self._session.request(
                    method, url, headers=headers, json=json, **kwargs
                ) as resp2:
                    resp = resp2

            if resp.status != 200:
                raise RuntimeError(f"请求失败 [{resp.status}]: {await resp.text()}")

            # 面板接口统一返回 {"code":0, "msg":"...", "data":...}
            body = await resp.json()
            print(body)
            if body.get("status") != "ok":
                raise RuntimeError(f"业务错误: {body.get('msg')}")
            return body.get("data")

    async def _ensure_token(self) -> str:
        """返回可用 token，必要时自动登录"""
        now = time.time()
        if (
            self._token is None
            or self._token_ts is None
            or now - self._token_ts > self.TOKEN_VALID_THRESHOLD
        ):
            self._token = await self._login()
            self._token_ts = now
        return self._token

    async def _login(self) -> str:
        """执行登录并返回新 token"""
        dbc = self.context.get_config()["dashboard"]
        payload = {"username": dbc["username"], "password": dbc["password"]}
        if self._session is None:
            raise RuntimeError("请使用 async with DashboardClient() 初始化会话")
        async with self._session.post(self.login_url, json=payload) as resp:
            if resp.status != 200:
                raise RuntimeError(f"登录失败 [{resp.status}]: {await resp.text()}")

            data = await resp.json()
            token = data.get("data", {}).get("token")
            if not token:
                raise RuntimeError(f"登录响应异常: {data}")
            logger.info("登录成功，Token 已更新")
            return token
