# dashboard_client.py

import datetime
import os
from typing import Any

import aiohttp
import jwt

from astrbot.api import logger
from astrbot.core.star.context import Context
from astrbot.core.star.star import StarMetadata


class DashboardClient:
    """
    面板 HTTP 客户端
    - 复用 aiohttp.ClientSession
    - 使用 Dashboard JWT 调用内部接口，不再通过用户名密码登录
    """

    def __init__(self, context: Context):
        self.context = context
        self.stars: list[StarMetadata] = context.get_all_stars()
        self.star_manager = self.context._star_manager

        dbc = context.get_config().get("dashboard", {})
        self.host = dbc.get("host", "127.0.0.1")
        port_value = os.environ.get("DASHBOARD_PORT") or dbc.get("port", 6185)
        self.port = int(port_value)
        if self.host == "0.0.0.0":
            self.host = "127.0.0.1"

        # 接口地址
        self.restart_url = f"http://{self.host}:{self.port}/api/stat/restart-core"

        # 缓存用
        self._session: aiohttp.ClientSession | None = None

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
        json: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """统一网络请求：使用本地生成的 Dashboard JWT 鉴权。"""
        if self._session is None:
            raise RuntimeError("请先用 DashboardClient.initialize() 初始化会话")

        token = self._generate_jwt()
        headers = {"Authorization": f"Bearer {token}"}

        async with self._session.request(
            method, url, headers=headers, json=json, **kwargs
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"请求失败 [{resp.status}]: {await resp.text()}")

            body = await resp.json()
            if body.get("status") != "ok":
                raise RuntimeError(
                    f"业务错误: {body.get('message') or body.get('msg')}"
                )
            return body.get("data")

    def _generate_jwt(self) -> str:
        """为本地 Dashboard 请求生成 JWT。"""
        dbc = self.context.get_config()["dashboard"]
        username = dbc.get("username")
        jwt_secret = dbc.get("jwt_secret")
        if not username or not jwt_secret:
            raise RuntimeError("Dashboard 用户名或 jwt_secret 未配置，无法生成鉴权令牌")

        payload = {
            "username": username,
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=7),
        }
        token = jwt.encode(payload, jwt_secret, algorithm="HS256")
        logger.debug("已为重启请求生成本地 Dashboard JWT")
        return token
