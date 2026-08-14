# config.py
from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, get_type_hints

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.context import Context


class ConfigNode:
    _SCHEMA_CACHE: dict[type, dict[str, type]] = {}
    _FIELDS_CACHE: dict[type, set[str]] = {}

    @classmethod
    def _schema(cls) -> dict[str, type]:
        return cls._SCHEMA_CACHE.setdefault(cls, get_type_hints(cls))

    @classmethod
    def _fields(cls) -> set[str]:
        return cls._FIELDS_CACHE.setdefault(
            cls,
            {k for k in cls._schema() if not k.startswith("_")},
        )

    def __init__(self, data: MutableMapping[str, Any]):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_children", {})
        for key, tp in self._schema().items():
            if key.startswith("_"):
                continue
            if key in data:
                continue
            if hasattr(self.__class__, key):
                continue
            logger.warning(f"[config:{self.__class__.__name__}] miss key: {key}")

    def __getattr__(self, key: str) -> Any:
        if key in self._fields():
            value = self._data.get(key)
            tp = self._schema().get(key)

            if isinstance(tp, type) and issubclass(tp, ConfigNode):
                children: dict[str, ConfigNode] = self.__dict__["_children"]
                if key not in children:
                    if not isinstance(value, MutableMapping):
                        raise TypeError(
                            f"[config:{self.__class__.__name__}] "
                            f"key {key} need dict，but {type(value).__name__}"
                        )
                    children[key] = tp(value)
                return children[key]

            return value

        if key in self.__dict__:
            return self.__dict__[key]

        raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        if key in self._fields():
            self._data[key] = value
            return
        object.__setattr__(self, key, value)

    def save_config(self) -> None:
        if not isinstance(self._data, AstrBotConfig):
            raise RuntimeError(
                f"{self.__class__.__name__}.save_config() only support AstrBotConfig"
            )
        self._data.save_config()


class RestartCache(ConfigNode):
    platform_id: str
    umo: str
    start_ts: float
    scene: str


class PluginConfig(ConfigNode):
    timed_restart: bool
    restart_cron: str
    show_memory: bool
    cache: RestartCache

    def __init__(self, cfg: AstrBotConfig, context: Context):
        super().__init__(cfg)
        self.context = context

    def valid_cache(self) -> bool:
        return (
            bool(self.cache.umo)
            and bool(self.cache.platform_id)
            and bool(self.cache.start_ts)
        )

    def clear_cache(self) -> None:
        self.cache.platform_id = ""
        self.cache.umo = ""
        self.cache.start_ts = 0
        self.cache.scene = ""
        self.save_config()

    def switch_timed_restart(self, enable: bool) -> None:
        self.timed_restart = enable
        self.save_config()
