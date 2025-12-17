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
from astrbot.core.star.star_manager import PluginManager

from .dashboard_client import DashboardClient
from .restart_scheduler import RestartScheduler
from .utils import cron_to_human, get_memory_info


class RestartPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.star_manager: PluginManager = self.context._star_manager  # type: ignore
        self.config = config
        self.cache: dict[str, Any] = config.get("restart_cache", {})
        self.restart_cron = config.get("restart_cron")

    # ================== 生命周期 ==================

    async def initialize(self):
        self.dashboard = DashboardClient(self.context)
        await self.dashboard.initialize()
        self.scheduler = RestartScheduler(self.context, self.config, self.dashboard)
        if self.config["restart_switch"]:
            await self.scheduler.start()

    async def terminate(self):
        await self.dashboard.terminate()
        await self.scheduler.shutdown()
        logger.info("重启插件已终止")

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
        msg = f"AstrBot重启完成（耗时{elapsed:.2f}秒）"

        if self.config["show_memory_info"]:
            memory_info = get_memory_info()
            msg += f"\n内存：{memory_info}"

        await self.context.send_message(
            session=restart_umo,
            message_chain=MessageChain([Plain(msg)]),
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
        self.cache["platform_id"] = event.get_platform_id()
        self.cache["umo"] = event.unified_msg_origin
        self.cache["start_ts"] = time.time()
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
            yield event.plain_result(
                f"已开启定时重启: {cron_to_human(self.config['restart_cron'])}"
            )
            await self.scheduler.start()
        else:
            self.config["restart_switch"] = False
            self.config.save_config()
            yield event.plain_result("已关闭定时重启")
            await self.scheduler.shutdown()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重载")
    async def reload_plugin(self, event: AstrMessageEvent, target: str | int | None = None):
        """重载 <插件名|序号|空|all>"""
        from astrbot.core.star.star import star_registry as sr

        # 过滤内置插件
        visible = [m for m in sr if not m.reserved]

        # 1. 无参数 -> 展示带序号的插件列表（展示名优先）
        if target is None:
            lines = ["需指定插件序号："]
            for idx, meta in enumerate(visible, start=1):
                show = meta.display_name or meta.name
                lines.append(f"{idx}. {show}")
            await event.send(event.plain_result("\n".join(lines)))
            return

        # 2. 统一把 target 解析成“内部名” plugin_key
        plugin_key = None
        if isinstance(target, int) or str(target).isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(visible):
                plugin_key = visible[idx].name
            else:
                await event.send(event.plain_result("序号超出范围"))
                return

        elif str(target).lower() == "all":
            plugin_key = None

        else:  # 字符串：支持展示名或内部名
            tgt = str(target)
            for meta in sr:
                if tgt in (meta.display_name, meta.name):
                    plugin_key = meta.name
                    break
            if plugin_key is None:
                await event.send(event.plain_result("未找到该插件"))
                return

        # 3. 真正重载
        success, error_message = await self.star_manager.reload(plugin_key)

        # 4. 结果回显：优先用展示名，没有再剥前缀
        if plugin_key is None:
            show_name = "所有插件"
        else:
            if meta := next((m for m in sr if (m.name or m.module_path) == plugin_key), None):
                show_name = str(meta.display_name or meta.name).removeprefix("astrbot_plugin_")

        if success:
            yield event.plain_result(f"{show_name}重载成功")
        else:
            yield event.plain_result(f"{show_name}重载失败：{error_message}")
