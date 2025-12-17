# restart_scheduler.py
import zoneinfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.context import Context

from .dashboard_client import DashboardClient


class RestartScheduler:
    def __init__(
        self, context: Context, config: AstrBotConfig, dashboard: DashboardClient
    ):
        self.context = context
        self.config = config

        self.restart_cron = config.get("restart_cron")

        self._scheduler: AsyncIOScheduler | None = None
        self.dashboard = dashboard

        timezone = self.context.get_config().get("timezone")
        try:
            self.tz = zoneinfo.ZoneInfo(timezone) if timezone else None
        except Exception:
            logger.warning("时区配置无效，使用默认时区")
            self.tz = None

    # ================== 生命周期 ==================

    async def start(self):
        self._scheduler = AsyncIOScheduler(timezone=self.tz)
        self._register_jobs()
        self._scheduler.start()

    async def shutdown(self):
        if self._scheduler:
            self._scheduler.shutdown()

    # ================== job 管理 ==================

    def _register_jobs(self):
        scheduler = self._scheduler
        if scheduler is None:
            return

        job_id = "restart_cron_job"

        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        if not self.restart_cron:
            logger.debug("未配置 restart_cron，自动重启已禁用")
            return

        try:
            trigger = CronTrigger.from_crontab(self.restart_cron)
        except Exception as e:
            logger.error(f"Cron 表达式错误：{self.restart_cron} ({e})")
            return

        scheduler.add_job(
            self.restart,
            trigger=trigger,
            id=job_id,
        )

        logger.debug(f"已注册 Cron 自动重启：{self.restart_cron}")

    # ================== 动作 ==================

    async def restart(self):
        await self.dashboard.restart()
