import asyncio
import os
import zoneinfo
import aiohttp
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot import logger
from astrbot.api.event import filter
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler


@register("astrbot_plugin_restart", "Zhalslar", "重启", "1.0.0")
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
        print("平台加载完成时，发送上一轮重启完成的消息")
        print(self.config.get("restart_umo"))
        await asyncio.sleep(5)
        if restart_umo := self.config.get("restart_umo"):
            await self.context.send_message(
                session=restart_umo,
                message_chain=MessageChain([Plain("AstrBot 重启完成")]),
            )
        self.config["restart_umo"] = ""
        self.config.save_config()

    async def _get_auth_token(self):
        """获取认证token"""
        # 根据面板配置生成登录数据、 URL
        username = self.dbc.get("username", "astrbot")
        password = self.dbc.get("password", "77b90590a8945a7d36c963981a307dc9")
        login_data = {"username": username, "password": password}
        login_url = f"http://{self.host}:{self.port}/api/auth/login"

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
        except Exception as e:
            logger.error(f"❌ 发送重启请求时出错: {e}")

    def _register_jobs(self):
        """根据配置注册定时任务"""
        # 清理旧任务，避免重复
        self.scheduler.remove_all_jobs()

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
        await self.restart_core()
        # 会话标记
        self.config["restart_umo"] = event.unified_msg_origin
        self.config.save_config()
        logger.info(f"重启会话标记：{self.config['restart_umo']}")

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
