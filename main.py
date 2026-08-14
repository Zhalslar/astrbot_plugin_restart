# restart_plugin.py
import asyncio
import time

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent, MessageSesion
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)
from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import (
    QQOfficialPlatformAdapter,
)
from astrbot.core.platform.sources.qqofficial_webhook.qo_webhook_adapter import (
    QQOfficialWebhookPlatformAdapter,
)
from astrbot.core.star.star_manager import PluginManager

from .core.config import PluginConfig
from .core.dashboard_client import DashboardClient
from .core.restart_scheduler import RestartScheduler
from .core.utils import cron_to_human, get_memory_info


class RestartPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.cfg = PluginConfig(config, context)
        self.star_manager: PluginManager = self.context._star_manager  # type: ignore
        self.dashboard = DashboardClient(context)
        self.scheduler = RestartScheduler(
            self.context,
            self.cfg.restart_cron,
            self.dashboard,
        )

    async def initialize(self):
        await self.dashboard.initialize()
        if self.cfg.timed_restart:
            await self.scheduler.start()

    async def terminate(self):
        await self.dashboard.terminate()
        await self.scheduler.shutdown()

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        if not self.cfg.valid_cache():
            return

        cache = self.cfg.cache
        platform = self.context.get_platform_inst(cache.platform_id)
        if platform is None:
            return

        if isinstance(platform, AiocqhttpAdapter):
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
        elif isinstance(
            platform, QQOfficialPlatformAdapter | QQOfficialWebhookPlatformAdapter
        ):
            session = MessageSesion.from_str(cache.umo)
            scene = cache.scene or (
                "group"
                if session.message_type == MessageType.GROUP_MESSAGE
                else "friend"
            )
            platform.remember_session_scene(session.session_id, scene)
            client = platform.get_client()
            try:
                for i in range(100):
                    if client._connection:
                        break
                    if client.is_closed():
                        return
                    await asyncio.sleep(0.1)
                else:
                    logger.warning("QQ 官方机器人连接等待超时")
                    return
            except asyncio.CancelledError:
                raise
        else:
            return

        elapsed = time.time() - cache.start_ts
        msg = f"AstrBot重启完成（耗时{elapsed:.2f}秒）"

        if self.cfg.show_memory:
            memory_info = get_memory_info()
            msg += f"\n内存：{memory_info}"

        await self.context.send_message(
            session=cache.umo,
            message_chain=MessageChain([Plain(msg)]),
        )
        self.cfg.clear_cache()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重启", alias={"restart"})
    async def restart_system(self, event: AstrMessageEvent):
        """重启Astrbot"""
        await event.send(event.plain_result("正在重启 AstrBot…"))
        cache = self.cfg.cache
        cache.platform_id = event.get_platform_id()
        cache.umo = event.unified_msg_origin
        cache.start_ts = time.time()
        cache.scene = ""
        if event.get_platform_name() == "qq_official":
            raw_message = getattr(event.message_obj, "raw_message", None)
            if getattr(raw_message, "group_openid", None):
                cache.scene = "group"
            elif getattr(raw_message, "channel_id", None):
                cache.scene = "channel"
            else:
                cache.scene = "friend"
        self.cfg.save_config()

        await self.dashboard.restart()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("定时重启")
    async def schedule_restart(self, event: AstrMessageEvent, mode: str | None = None):
        """定时重启 开/关"""
        if mode not in ["开", "关"]:
            await event.send(event.plain_result("正确格式：定时重启 开/关"))
            return
        is_restart = mode == "开"
        self.cfg.switch_timed_restart(is_restart)
        if is_restart:
            yield event.plain_result(
                f"已开启定时重启: {cron_to_human(self.cfg.restart_cron)}"
            )
            await self.scheduler.start()
        else:
            yield event.plain_result("已关闭定时重启")
            await self.scheduler.shutdown()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重载")
    async def reload_plugin(
        self, event: AstrMessageEvent, target: str | int | None = None
    ):
        """重载 <插件名|序号|空|all>"""
        from astrbot.core.star.star import star_registry as sr

        # 过滤内置插件
        visible = [m for m in sr if not m.reserved]
        if not visible:
            yield event.plain_result("暂无插件")
            return

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
                yield event.plain_result("序号超出范围")
                return

        elif str(target).lower() == "all":
            plugin_key = None

        else:  # 字符串：支持展示名或内部名
            tgt = str(target)
            for meta in sr:
                if tgt in str(meta.display_name) or tgt in str(meta.name):
                    plugin_key = meta.name
                    break
            if plugin_key is None:
                yield event.plain_result("未找到该插件")
                return

        # 3. 真正重载
        success, error_message = await self.star_manager.reload(plugin_key)

        # 4. 结果回显：优先用展示名，没有再剥前缀
        if plugin_key is None:
            show_name = "所有插件"
        else:
            if meta := next(
                (m for m in sr if (m.name or m.module_path) == plugin_key), None
            ):
                show_name = str(meta.display_name or meta.name).removeprefix(
                    "astrbot_plugin_"
                )
            else:
                show_name = plugin_key.removeprefix("astrbot_plugin_")

        if success:
            yield event.plain_result(f"{show_name}重载成功")
        else:
            yield event.plain_result(f"{show_name}重载失败：{error_message}")
