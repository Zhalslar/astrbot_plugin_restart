import asyncio
import os
import time
import zoneinfo
import aiohttp
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)

@register("astrbot_plugin_restart", "Zhalslar", "重启", "...")
class RestartPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.restart_interval = self.config.get("restart_interval")  # 秒数，例如 86400
        self.restart_time = self.config.get("restart_time")  # 字符串 "HH:MM"

        # 根据面板配置生成重启URL
        self.dbc = self.context.get_config().get("dashboard", {})
        self.host = self.dbc.get("host", "127.0.0.1")
        self.port = self.dbc.get("port", 6185)
        # 检查环境变量覆盖
        if env_port := os.environ.get("DASHBOARD_PORT"):
            self.port = int(env_port)
        if self.host == "0.0.0.0":
            self.host = "127.0.0.1"
        self.restart_url = f"http://{self.host}:{self.port}/api/stat/restart-core"

    async def initialize(self):
        # 创建调度器
        timezone = self.context.get_config().get("timezone")
        try:
            target_timezone = zoneinfo.ZoneInfo(timezone) if timezone else None
        except Exception as e:
            logger.error(f"时区设置错误: {e}, 使用本地时区")
            target_timezone = None
        self.scheduler = AsyncIOScheduler(timezone=target_timezone)
        self.session = aiohttp.ClientSession()
        # 注册任务
        self._register_jobs()
        self.scheduler.start()

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        """平台加载完成时，发送上一轮重启完成的消息"""
        restart_umo = self.config.get("restart_umo")
        platform_id = self.config.get("platform_id")
        restart_start_ts = self.config.get("restart_start_ts")
        if not restart_umo or not platform_id or not restart_start_ts:
            return

        platform = self.context.get_platform_inst(platform_id)
        if not isinstance(platform, AiocqhttpAdapter):
            logger.warning("未找到 aiocqhttp 平台实例，跳过重启提示")
            return
        client = platform.get_client()
        if not client:
            logger.warning("未找到 CQHttp 实例，跳过重启提示")
            return

        # 等待 ws 连接完成
        ws_connected = asyncio.Event()

        @client.on_websocket_connection
        def _(_):  # 连接成功时触发
            ws_connected.set()

        try:
            await asyncio.wait_for(ws_connected.wait(), timeout=10)
        except asyncio.TimeoutError:
            logger.warning(
                "等待 aiocqhttp WebSocket 连接超时，可能未能发送重启完成提示。"
            )

        # 计算耗时
        elapsed = time.time() - float(restart_start_ts)

        # 发消息
        await self.context.send_message(
            session=restart_umo,
            message_chain=MessageChain(
                [Plain(f"AstrBot重启完成（耗时{elapsed:.2f}秒）")]
            ),
        )

        # 清理持久化配置
        self.config["restart_umo"] = ""
        self.config["restart_start_ts"] = 0
        self.config.save_config()

    async def _get_auth_token(self):
        """获取认证token"""
        login_url = f"http://{self.host}:{self.port}/api/auth/login"
        login_data = {
            "username": self.dbc["username"],
            "password": self.dbc["password"],
        }
        async with self.session.post(login_url, json=login_data) as response:
            if response.status == 200:
                data = await response.json()
                if data and data.get("status") == "ok" and "data" in data:
                    return data["data"]["token"]
                else:
                    raise Exception(f"登录响应格式错误: {data}")
            else:
                text = await response.text()
                raise Exception(f"登录失败，状态码: {response.status}, 响应: {text}")

    async def restart_core(self):
        try:
            token = await self._get_auth_token()
            headers = {"Authorization": f"Bearer {token}"}
            async with self.session.post(self.restart_url, headers=headers) as response:
                if response.status == 200:
                    logger.info("✅ 系统重启请求已发送")
                else:
                    logger.error(f"❌ 重启请求失败，状态码: {response.status}")
                    raise RuntimeError(f"重启请求失败，状态码: {response.status}")
        except Exception as e:
            logger.error(f"❌ 发送重启请求时出错: {e}")
            raise e

    def _register_jobs(self):
        """根据配置注册定时任务"""
        # 清理旧任务，避免重复
        if self.scheduler.get_job("restart_interval_job"):
            self.scheduler.remove_job("restart_interval_job")
        if self.scheduler.get_job("restart_time_job"):
            self.scheduler.remove_job("restart_time_job")

        if self.restart_interval:
            logger.info(f"注册间隔重启任务：每 {self.restart_interval} 秒重启一次")
            self.scheduler.add_job(
                self.restart_core,
                "interval",
                seconds=self.restart_interval,
                id="restart_interval_job",
            )

        if self.restart_time:
            try:
                hour, minute = self.restart_time.split(":")
                logger.info(f"注册每日定时重启任务：每天 {self.restart_time} 重启一次")
                self.scheduler.add_job(
                    self.restart_core,
                    "cron",
                    hour=hour,
                    minute=minute,
                    id="restart_time_job",
                )
            except ValueError:
                logger.error(f"定时重启时间格式错误：{self.restart_time}，应为 HH:MM")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重启", alias={"restart"})
    async def restart_system(self, event: AstrMessageEvent):
        """重启astrbot"""
        yield event.plain_result("正在重启AstrBot...")

        self.config["platform_id"] = event.get_platform_id()
        self.config["restart_umo"] = event.unified_msg_origin
        self.config["restart_start_ts"] = time.time()
        self.config.save_config()
        logger.info(
            "手动重启：已记录 platform_id、restart_umo 与 restart_start_ts，准备重启"
        )
        try:
            await self.restart_core()
        except Exception as e:
            yield event.plain_result(f"重启失败：{e}")
            return

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("定时重启", alias={"schedule_restart"})
    async def schedule_restart(
        self, event: AstrMessageEvent, input: str | int | None = None
    ):
        """定时重启 HH:MM / 重启间隔"""
        if isinstance(input, str) and ":" in input:
            self.config["restart_time"] = input.strip()
            self.config.save_config()
            yield event.plain_result(f"已设置定时重启：每天 {input} 重启一次")

        elif isinstance(input, int):
            self.config["restart_interval"] = int(input)
            self.config.save_config()
            yield event.plain_result(f"已设置定时重启：每隔 {input} 秒重启一次")

        else:
            yield event.plain_result("输入格式错误，请输入 HH:MM 或数字")
            return

        # 重新注册任务
        self._register_jobs()
        logger.info("定时重启任务已更新")

    async def terminate(self):
        """插件终止时清理调度器"""
        if hasattr(self, "scheduler"):
            self.scheduler.shutdown()
        if hasattr(self, "session"):
            await self.session.close()
        logger.info("定时重启插件已终止")
