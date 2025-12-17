# dashboard_client.py
import os

import aiohttp

from astrbot.api import logger
from astrbot.core.star.context import Context


class DashboardClient:
    def __init__(self, context: Context):
        self.context = context

        dbc = context.get_config().get("dashboard", {})
        self.host = dbc.get("host", "127.0.0.1")
        self.port = int(os.environ.get("DASHBOARD_PORT", dbc.get("port", 6185)))

        if self.host == "0.0.0.0":
            self.host = "127.0.0.1"

        self.login_url = f"http://{self.host}:{self.port}/api/auth/login"
        self.restart_url = f"http://{self.host}:{self.port}/api/stat/restart-core"

    async def restart(self):
        """
        一次性：登录 → 重启 → 关闭 session
        """
        async with aiohttp.ClientSession() as session:
            token = await self._login(session)
            await self._restart_core(session, token)

    async def _login(self, session: aiohttp.ClientSession) -> str:
        dbc = self.context.get_config()["dashboard"]
        payload = {
            "username": dbc["username"],
            "password": dbc["password"],
        }

        async with session.post(self.login_url, json=payload) as resp:
            if resp.status != 200:
                raise RuntimeError(f"登录失败: {resp.status}")

            data = await resp.json()
            token = data.get("data", {}).get("token")

            if not token:
                raise RuntimeError(f"登录响应异常: {data}")

            return token

    async def _restart_core(self, session: aiohttp.ClientSession, token: str):
        headers = {"Authorization": f"Bearer {token}"}

        async with session.post(self.restart_url, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"重启失败: {resp.status}")

            logger.info("✅ 已发送重启请求")
