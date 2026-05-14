"""485 设备控制与下位机协议适配层。

当前项目历史上只有串口预留接口，没有可复用的运动控制实现。本模块把
回零、三轴点动、坐标读取、光源控制和槽位移动统一收口，UI 层只关心
``DeviceCommandResult``，不直接拼协议帧。

协议说明
--------
下位机最终协议尚未在仓库中固化，因此这里先使用可读 ASCII 帧：

- ``HOME`` -> ``HOME_DONE``
- ``MOVE X 1.000`` -> ``MOVE_DONE``
- ``GET_POS`` -> ``POS X=0.000 Y=0.000 Z=0.000``
- ``MOVE_TO X=0.000 Y=0.000 Z=0.000`` -> ``MOVE_DONE``
- ``LIGHT 1 3.30`` -> ``LIGHT_OK``

后续若下位机改用二进制帧或 CRC，只需要替换本模块的封包/解析方法。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DeviceCommandResult:
    """统一返回给 UI 的设备命令结果。"""

    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class DeviceController:
    """封装 485 通讯、设备初始化状态和运动安全检查。"""

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self.serial_port = config.get("serial_port")
        self.baudrate = int(config.get("serial_baudrate", 9600))
        self.serial_timeout = float(config.get("serial_timeout", 1.0))
        self.command_timeout = float(config.get("device_command_timeout_sec", 5.0))
        self.homing_timeout = float(config.get("homing_timeout_sec", 60.0))
        self.serial_enabled = bool(config.get("serial_enabled", bool(self.serial_port)))
        # 当前阶段默认走模拟回零，后续上线真实设备时在 app_config.json 中把
        # device_simulation 改为 false，并配置 serial_enabled/serial_port。
        self.simulation_enabled = bool(config.get("device_simulation", True))

        self._serial = None
        self.device_initialized = False
        self.current_position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.light_config = {"light1Voltage": 0.0, "light2Voltage": 0.0}

    @property
    def is_initialized(self):
        """设备是否已完成开机回零。"""
        return self.device_initialized

    def open(self):
        """打开 485 串口；离线联调模式下不实际打开串口。"""
        if self.simulation_enabled:
            logger.warning("设备控制处于离线联调模式，未连接真实 485 串口。")
            return DeviceCommandResult(True, "离线联调模式")

        if self._serial is not None and getattr(self._serial, "is_open", False):
            return DeviceCommandResult(True, "485 串口已连接")

        if not self.serial_enabled or not self.serial_port:
            return DeviceCommandResult(False, "未配置 485 串口，无法与下位机通讯。")

        try:
            import serial
        except ImportError:
            return DeviceCommandResult(False, "缺少 pyserial 依赖，无法打开 485 串口。")

        try:
            self._serial = serial.Serial(
                port=self.serial_port,
                baudrate=self.baudrate,
                timeout=self.serial_timeout,
            )
        except Exception as exc:
            logger.exception("打开 485 串口失败")
            return DeviceCommandResult(False, f"打开 485 串口失败：{exc}")

        logger.info("485 串口已打开 port=%s baudrate=%s", self.serial_port, self.baudrate)
        return DeviceCommandResult(True, "485 串口已连接")

    def close(self):
        """关闭串口资源。"""
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                logger.exception("关闭 485 串口失败")
            self._serial = None

    def home(self, timeout_sec=None):
        """执行开机回零，并只在收到回零完成反馈后设置初始化状态。"""
        timeout_sec = timeout_sec or self.homing_timeout
        result = self._send_and_wait("HOME", "HOME_DONE", timeout_sec)
        if result.success:
            self.device_initialized = True
            self.current_position = {"x": 0.0, "y": 0.0, "z": 0.0}
            result.message = "设备回零完成。"
            logger.info("设备回零完成，坐标基准已复位。")
        return result

    def require_initialized(self):
        """运动类指令前置检查。"""
        if not self.device_initialized:
            return DeviceCommandResult(False, "设备尚未回零，禁止执行运动控制。")
        return DeviceCommandResult(True, "设备已回零")

    def move_axis(self, axis, distance_mm, timeout_sec=None):
        """按给定位移点动 X/Y/Z 轴。"""
        ready = self.require_initialized()
        if not ready.success:
            return ready

        axis = str(axis).upper()
        if axis not in {"X", "Y", "Z"}:
            return DeviceCommandResult(False, f"非法轴名称：{axis}")

        try:
            distance = float(distance_mm)
        except (TypeError, ValueError):
            return DeviceCommandResult(False, "点动距离不是合法数字。")

        command = f"MOVE {axis} {distance:.3f}"
        result = self._send_and_wait(command, "MOVE_DONE", timeout_sec or self.command_timeout)
        if result.success:
            key = axis.lower()
            self.current_position[key] = round(self.current_position[key] + distance, 3)
            result.data = self.current_position.copy()
            result.message = f"{axis} 轴点动完成，当前坐标：{self._format_position()}"
        return result

    def move_to_coordinate(self, x, y, z, timeout_sec=None):
        """移动到指定三轴坐标。"""
        ready = self.require_initialized()
        if not ready.success:
            return ready

        try:
            target = {"x": float(x), "y": float(y), "z": float(z)}
        except (TypeError, ValueError):
            return DeviceCommandResult(False, "目标坐标不是合法数字。")

        command = "MOVE_TO X={x:.3f} Y={y:.3f} Z={z:.3f}".format(**target)
        result = self._send_and_wait(command, "MOVE_DONE", timeout_sec or self.command_timeout)
        if result.success:
            self.current_position = target
            result.data = target.copy()
            result.message = f"已移动到目标坐标：{self._format_position()}"
        return result

    def request_current_position(self, timeout_sec=None):
        """向下位机请求当前三轴坐标。"""
        ready = self.require_initialized()
        if not ready.success:
            return ready

        result = self._send_and_wait("GET_POS", "POS", timeout_sec or self.command_timeout)
        if not result.success:
            return result

        position = self._parse_position(result.data.get("raw", ""))
        if position is None:
            return DeviceCommandResult(False, "下位机返回坐标格式错误。", {"raw": result.data.get("raw", "")})

        self.current_position = position
        return DeviceCommandResult(True, f"当前坐标：{self._format_position()}", position.copy())

    def set_light_voltage(self, channel, voltage, timeout_sec=None):
        """设置指定光源通道电压。"""
        try:
            channel = int(channel)
            voltage = float(voltage)
        except (TypeError, ValueError):
            return DeviceCommandResult(False, "光源通道或电压不是合法数字。")

        if channel not in {1, 2}:
            return DeviceCommandResult(False, f"非法光源通道：{channel}")
        if voltage < 0:
            return DeviceCommandResult(False, "光源电压不能为负数。")

        command = f"LIGHT {channel} {voltage:.2f}"
        result = self._send_and_wait(command, "LIGHT_OK", timeout_sec or self.command_timeout)
        if result.success:
            key = "light1Voltage" if channel == 1 else "light2Voltage"
            self.light_config[key] = voltage
            result.data = self.light_config.copy()
            result.message = f"光源 {channel} 电压已设置为 {voltage:.2f}。"
        return result

    def _send_and_wait(self, command, expected_prefix, timeout_sec):
        link = self.open()
        if not link.success:
            logger.error("设备通讯不可用：%s", link.message)
            return link

        try:
            self._write_command(command)
        except Exception as exc:
            logger.exception("发送 485 指令失败")
            return DeviceCommandResult(False, f"发送 485 指令失败：{exc}")

        if self.simulation_enabled:
            return self._simulate_response(command, expected_prefix)

        deadline = time.monotonic() + float(timeout_sec)
        while time.monotonic() < deadline:
            line = self._read_line()
            if not line:
                time.sleep(0.03)
                continue
            if line.startswith(expected_prefix) or expected_prefix in line:
                return DeviceCommandResult(True, "收到下位机反馈。", {"raw": line})
            logger.debug("忽略非目标反馈：%s", line)

        logger.error("等待下位机反馈超时 command=%s expected=%s", command, expected_prefix)
        return DeviceCommandResult(False, f"等待下位机反馈超时：{expected_prefix}")

    def _write_command(self, command):
        logger.info("485 发送指令：%s", command)
        if self.simulation_enabled:
            return
        payload = (command + "\r\n").encode("ascii", errors="ignore")
        self._serial.write(payload)
        self._serial.flush()

    def _read_line(self):
        if self._serial is None:
            return ""
        raw = self._serial.readline()
        if not raw:
            return ""
        line = raw.decode("ascii", errors="ignore").strip()
        if line:
            logger.info("485 接收数据：%s", line)
        return line

    def _simulate_response(self, command, expected_prefix):
        time.sleep(0.2)
        if command == "HOME":
            raw = "HOME_DONE"
        elif command == "GET_POS":
            raw = (
                "POS X={x:.3f} Y={y:.3f} Z={z:.3f}"
                .format(**self.current_position)
            )
        elif command.startswith("MOVE") or command.startswith("MOVE_TO"):
            raw = "MOVE_DONE"
        elif command.startswith("LIGHT"):
            raw = "LIGHT_OK"
        else:
            raw = expected_prefix
        logger.info("485 模拟反馈：%s", raw)
        return DeviceCommandResult(True, "收到模拟下位机反馈。", {"raw": raw})

    def _parse_position(self, raw):
        if not raw:
            return self.current_position.copy() if self.simulation_enabled else None

        patterns = [
            r"X\s*[=:]\s*(-?\d+(?:\.\d+)?)\D+Y\s*[=:]\s*(-?\d+(?:\.\d+)?)\D+Z\s*[=:]\s*(-?\d+(?:\.\d+)?)",
            r"POS\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw, re.IGNORECASE)
            if match:
                return {
                    "x": float(match.group(1)),
                    "y": float(match.group(2)),
                    "z": float(match.group(3)),
                }
        return None

    def _format_position(self):
        return "X={x:.3f}, Y={y:.3f}, Z={z:.3f}".format(**self.current_position)
