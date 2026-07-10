"""配置驱动的灯光控制预留接口。"""

from __future__ import annotations

import logging
from typing import Any

from .modbus_motion_controller import d_addr

logger = logging.getLogger(__name__)


def _config_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "启用", "开"}:
        return True
    if text in {"0", "false", "no", "off", "禁用", "关"}:
        return False
    return default


def _config_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ConfigurableLightController:
    """通过配置预留灯光寄存器写入能力。

    当前寄存器未确定时，保持 ``red_light_enabled=false`` 或寄存器为空即可。
    后续只要在配置文件中补齐寄存器并启用，调用方无需变更。
    """

    def __init__(self, motion_controller=None, config: dict[str, Any] | None = None):
        self.motion_controller = motion_controller
        self.reload_config(config or {})
        self._last_red_light_on = None

    def reload_config(self, config: dict[str, Any] | None = None):
        config = config or {}
        self.red_light_enabled = _config_bool(config.get("red_light_enabled"), False)
        self.red_light_initial_on = _config_bool(config.get("red_light_initial_on"), False)
        self.red_light_switch_register = _config_optional_int(config.get("red_light_switch_register"))
        self.red_light_brightness_register = _config_optional_int(config.get("red_light_brightness_register"))
        self.red_light_on_value = int(config.get("red_light_on_value", 1))
        self.red_light_off_value = int(config.get("red_light_off_value", 0))
        self.red_light_brightness_initial = int(config.get("red_light_brightness_initial", 100))
        self.light_register_data_type = str(config.get("light_register_data_type", "s32")).strip().lower()

    def set_red_light(self, on: bool):
        """设置红灯开关；未启用或未配置寄存器时只记录日志。"""
        on = bool(on)
        self._last_red_light_on = on
        if not self.red_light_enabled:
            logger.debug("红灯控制未启用，跳过写入：on=%s", on)
            return False
        if self.motion_controller is None:
            logger.warning("红灯控制已启用，但未注入运动控制器。")
            return False
        if not getattr(self.motion_controller, "is_connected", False):
            logger.debug("红灯控制器尚未连接，跳过写入：on=%s", on)
            return False
        if self.red_light_switch_register is None:
            logger.warning("红灯控制已启用，但 red_light_switch_register 未配置。")
            return False

        value = self.red_light_on_value if on else self.red_light_off_value
        self._write_register(self.red_light_switch_register, value)
        return True

    def apply_initial_state(self):
        if not self.red_light_enabled:
            logger.debug("红灯控制未启用，跳过初始状态写入")
            return False
        if self.motion_controller is None or not getattr(self.motion_controller, "is_connected", False):
            logger.debug("红灯控制器尚未连接，跳过初始状态写入")
            return False
        if self.red_light_brightness_register is not None and self.red_light_enabled:
            self._write_register(self.red_light_brightness_register, self.red_light_brightness_initial)
        return self.set_red_light(self.red_light_initial_on)

    def _write_register(self, d_number: int, value: int):
        if self.light_register_data_type in {"s32", "int32", "32"}:
            self.motion_controller.write_32bit_register(d_addr(d_number), int(value))
            return
        if self.light_register_data_type in {"u16", "int16", "16"}:
            self.motion_controller._call_with_slave_id(
                "write_register",
                address=d_addr(d_number),
                value=int(value) & 0xFFFF,
            )
            return
        raise ValueError(f"不支持的灯光寄存器数据类型：{self.light_register_data_type}")
