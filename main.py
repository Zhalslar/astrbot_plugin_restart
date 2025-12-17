# restart_plugin.py
import asyncio
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)

from .dashboard_client import DashboardClient
from .restart_scheduler import RestartScheduler


class RestartPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.cache: dict[str, Any] = config.get("restart_cache", {})

    # ================== 生命周期 ==================

    async def initialize(self):
        self.dashboard = DashboardClient(self.context)
        self.scheduler = RestartScheduler(self.context, self.config, self.dashboard)
        if self.config["restart_switch"]:
            await self.scheduler.start()

    async def terminate(self):
        if self.scheduler:
            await self.scheduler.shutdown()
        logger.info("定时重启插件已终止")

    # ================== 重启完成通知 ==================

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        platform_id = self.cache.get("platform_id")
        restart_umo = self.cache.get("umo")
        restart_start_ts = self.cache.get("start_ts")

        if not restart_umo or not platform_id or not restart_start_ts:
            return

        platform = self.context.get_platform_inst(platform_id)
        if not isinstance(platform, AiocqhttpAdapter):
            return

        client = platform.get_client()
        if not client:
            return

        ws_connected = asyncio.Event()

        @client.on_websocket_connection
        def _(_):
            ws_connected.set()

        try:
            await asyncio.wait_for(ws_connected.wait(), timeout=10)
        except asyncio.TimeoutError:
            logger.warning("WebSocket 连接等待超时")

        elapsed = time.time() - float(restart_start_ts)

        await self.context.send_message(
            session=restart_umo,
            message_chain=MessageChain(
                [Plain(f"AstrBot重启完成（耗时{elapsed:.2f}秒）")]
            ),
        )
        self.cache["platform_id"] = ""
        self.cache["umo"] = ""
        self.cache["start_ts"] = 0
        self.config.save_config()

    # ================== 命令 ==================

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重启", alias={"restart"})
    async def restart_system(self, event: AstrMessageEvent):
        """重启Astrbot"""
        await event.send(event.plain_result("正在重启 AstrBot…"))

        self.config["platform_id"] = event.get_platform_id()
        self.config["restart_umo"] = event.unified_msg_origin
        self.config["restart_start_ts"] = time.time()
        self.config.save_config()

        await self.dashboard.restart()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("定时重启")
    async def schedule_restart(self, event: AstrMessageEvent, mode: str | None = None):
        """定时重启 开/关"""
        if mode not in ["开", "关"]:
            await event.send(event.plain_result("正确格式：定时重启 开/关"))
            return
        is_restart = mode == "开"
        if is_restart:
            self.config["restart_switch"] = True
            self.config.save_config()
            yield event.plain_result("已开启定时重启")
            await self.scheduler.start()
        else:
            self.config["restart_switch"] = False
            self.config.save_config()
            yield event.plain_result("已关闭定时重启")
            await self.scheduler.shutdown()

