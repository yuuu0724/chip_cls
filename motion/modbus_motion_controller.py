"""杰美康运动控制器 Modbus RTU 三轴控制。"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Any

try:
    from pymodbus.client import ModbusSerialClient
except ImportError:  # pymodbus 2.x
    try:
        from pymodbus.client.sync import ModbusSerialClient
    except ImportError:
        ModbusSerialClient = None

logger = logging.getLogger(__name__)

DEFAULT_PORT = "COM14"
DEFAULT_SLAVE_ID = 2
DEFAULT_BAUDRATE = 9600
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1
DEFAULT_TIMEOUT_SECONDS = 1.0

AXIS_D_REGISTERS = {
    "z": {"name": "Z", "trigger": 100, "move": 101, "position": 102, "speed": 103},
    "y": {"name": "Y", "trigger": 110, "move": 111, "position": 112, "speed": 113},
    "x": {"name": "X", "trigger": 120, "move": 121, "position": 122, "speed": 123},
}

AXIS_PULSES_PER_MM = {"x": 1000, "y": 500, "z": 2000}
AXIS_SOFT_LIMITS = {
    "x": (0, 230000),
    "y": (0, 210000),
    "z": (-90000, 60000),
}


@dataclass
class MotionCommandResult:
    """统一返回给 UI 的运动命令结果。"""

    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


def d_addr(d_number: int) -> int:
    """D99 的实际 Modbus 地址是 7040，后续每个 D 寄存器地址 +2。"""
    return 7040 + (int(d_number) - 99) * 2


def split_s32_to_words(value: int) -> tuple[int, int]:
    """将标准有符号 32 位整数拆成低 16 位、高 16 位。"""
    raw32 = int(value) & 0xFFFFFFFF
    low_word = raw32 & 0xFFFF
    high_word = (raw32 >> 16) & 0xFFFF
    return low_word, high_word


def combine_s32_from_words(low_word: int, high_word: int) -> int:
    """将低 16 位、高 16 位合成为标准有符号 32 位整数。"""
    raw32 = ((int(high_word) & 0xFFFF) << 16) | (int(low_word) & 0xFFFF)
    if raw32 >= 0x80000000:
        raw32 -= 0x100000000
    return raw32


def x_mm_to_pulses(mm: float) -> int:
    return int(float(mm) * AXIS_PULSES_PER_MM["x"])


def y_mm_to_pulses(mm: float) -> int:
    return int(float(mm) * AXIS_PULSES_PER_MM["y"])


def z_mm_to_pulses(mm: float) -> int:
    return int(float(mm) * AXIS_PULSES_PER_MM["z"])


class ModbusMotionController:
    """通过 Modbus RTU 控制 XYZ 三轴相对运动。"""

    def __init__(
        self,
        port: str = DEFAULT_PORT,
        slave_id: int = DEFAULT_SLAVE_ID,
        baudrate: int = DEFAULT_BAUDRATE,
        bytesize: int = DEFAULT_BYTESIZE,
        parity: str = DEFAULT_PARITY,
        stopbits: int = DEFAULT_STOPBITS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.port = port or DEFAULT_PORT
        self.slave_id = int(slave_id)
        self.baudrate = int(baudrate)
        self.bytesize = int(bytesize)
        self.parity = parity or DEFAULT_PARITY
        self.stopbits = int(stopbits)
        self.timeout = float(timeout)

        self.client = None
        self._lock = threading.RLock()
        self.device_initialized = False
        self.home_position = {"x": 0, "y": 0, "z": 0}
        self.last_position = {"x": 0, "y": 0, "z": 0}

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "ModbusMotionController":
        config = config or {}
        return cls(
            port=config.get("modbus_port") or config.get("serial_port") or DEFAULT_PORT,
            slave_id=config.get("modbus_slave_id", DEFAULT_SLAVE_ID),
            baudrate=config.get("modbus_baudrate") or config.get("serial_baudrate") or DEFAULT_BAUDRATE,
            bytesize=config.get("modbus_bytesize", DEFAULT_BYTESIZE),
            parity=config.get("modbus_parity", DEFAULT_PARITY),
            stopbits=config.get("modbus_stopbits", DEFAULT_STOPBITS),
            timeout=config.get("modbus_timeout") or config.get("serial_timeout") or DEFAULT_TIMEOUT_SECONDS,
        )

    @property
    def is_initialized(self):
        """设备是否已经触发真实机械回零。"""
        return self.device_initialized

    @property
    def is_connected(self):
        return self.client is not None and bool(getattr(self.client, "connected", True))

    def connect(self, port: str | None = None) -> MotionCommandResult:
        """连接 Modbus RTU 串口。"""
        if port:
            self.port = port
        if ModbusSerialClient is None:
            return MotionCommandResult(False, "缺少 pymodbus 依赖，请先安装 pymodbus。")

        with self._lock:
            if self.client is not None:
                try:
                    self.client.close()
                except Exception:
                    logger.exception("关闭旧 Modbus 连接失败")
                self.client = None

            self.client = ModbusSerialClient(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=self.timeout,
            )
            try:
                connected = self.client.connect()
            except Exception as exc:
                logger.exception("Modbus RTU 串口连接失败")
                self.client = None
                return MotionCommandResult(False, f"串口 {self.port} 连接失败：{exc}")

            if not connected:
                self.client = None
                return MotionCommandResult(False, f"串口 {self.port} 连接失败，请检查串口、接线和控制器电源。")

        logger.info("Modbus RTU 已连接 port=%s slave_id=%s baudrate=%s", self.port, self.slave_id, self.baudrate)
        return MotionCommandResult(True, f"串口 {self.port} 已连接")

    def open(self):
        """兼容旧调用名。"""
        return self.connect()

    def close(self):
        with self._lock:
            if self.client is not None:
                try:
                    self.client.close()
                    logger.info("Modbus RTU 串口已关闭")
                except Exception:
                    logger.exception("关闭 Modbus RTU 串口失败")
            self.client = None

    def d_addr(self, d_number: int) -> int:
        return d_addr(d_number)

    def _ensure_connected(self):
        if self.client is None:
            raise RuntimeError("尚未连接 Modbus RTU 控制器")

    def _call_with_slave_id(self, method_name: str, *args, **kwargs):
        self._ensure_connected()
        method = getattr(self.client, method_name)
        for slave_arg_name in ("slave", "unit", "device_id"):
            try:
                return method(*args, **kwargs, **{slave_arg_name: self.slave_id})
            except TypeError as exc:
                if slave_arg_name not in str(exc):
                    raise
        return method(*args, **kwargs)

    def write_32bit_register(self, addr: int, value: int):
        """写两个保持寄存器，低 16 位在前，高 16 位在后。"""
        with self._lock:
            low_word, high_word = split_s32_to_words(value)
            try:
                try:
                    response = self._call_with_slave_id(
                        "write_registers",
                        address=int(addr),
                        values=[low_word, high_word],
                    )
                except TypeError:
                    response = self._call_with_slave_id("write_registers", int(addr), [low_word, high_word])
            except Exception as exc:
                logger.exception("Modbus 写寄存器失败 addr=%s value=%s", addr, value)
                raise RuntimeError(f"写寄存器 {addr}/{addr + 1}={value} 失败：{exc}") from exc

            if response is None:
                raise RuntimeError(f"写寄存器 {addr}/{addr + 1}={value} 失败：无响应或超时")
            if response.isError():
                raise RuntimeError(f"写寄存器 {addr}/{addr + 1}={value} 失败：{response}")

            logger.info(
                "Modbus 写32位寄存器 addr=%s/%s value=%s low=%s high=%s",
                addr, addr + 1, value, low_word, high_word,
            )

    def read_32bit_register(self, addr: int) -> int:
        """读两个保持寄存器，并按标准有符号 32 位合成。"""
        with self._lock:
            try:
                try:
                    response = self._call_with_slave_id(
                        "read_holding_registers",
                        address=int(addr),
                        count=2,
                    )
                except TypeError:
                    response = self._call_with_slave_id("read_holding_registers", int(addr), count=2)
            except Exception as exc:
                logger.exception("Modbus 读寄存器失败 addr=%s", addr)
                raise RuntimeError(f"读寄存器 {addr}/{addr + 1} 失败：{exc}") from exc

            if response is None:
                raise RuntimeError(f"读寄存器 {addr}/{addr + 1} 失败：无响应或超时")
            if response.isError():
                raise RuntimeError(f"读寄存器 {addr}/{addr + 1} 失败：{response}")

            registers = getattr(response, "registers", None)
            if not registers or len(registers) < 2:
                raise RuntimeError(f"读寄存器 {addr}/{addr + 1} 失败：响应数据不完整")

            low_word, high_word = registers[0], registers[1]
            value = combine_s32_from_words(low_word, high_word)
            logger.info(
                "Modbus 读32位寄存器 addr=%s/%s value=%s low=%s high=%s",
                addr, addr + 1, value, low_word, high_word,
            )
            return value

    def home(
        self,
        stable_tolerance: int = 5,
        stable_samples: int = 8,
        poll_interval: float = 0.25,
        timeout: float | None = None,
    ) -> MotionCommandResult:
        """触发真实机械回零，并等待三轴反馈位置稳定后才返回成功。"""
        try:
            if self.client is None:
                result = self.connect()
                if not result.success:
                    return result

            with self._lock:
                home_addr = d_addr(99)
                self.write_32bit_register(home_addr, 1)
                time.sleep(0.05)
                self.write_32bit_register(home_addr, 0)
                positions = self._wait_until_positions_stable(
                    stable_tolerance=stable_tolerance,
                    stable_samples=stable_samples,
                    poll_interval=poll_interval,
                    timeout=timeout,
                )
                self.home_position = dict(positions)
                self.last_position = dict(positions)
                self.device_initialized = True
        except Exception as exc:
            logger.exception("机械回零触发失败")
            return MotionCommandResult(False, f"机械回零触发失败：{exc}")

        logger.info("机械回零完成，机械零点反馈=%s", self.home_position)
        return MotionCommandResult(True, "机械回零完成。", {"position": dict(self.last_position)})

    def _wait_until_positions_stable(
        self,
        stable_tolerance: int,
        stable_samples: int,
        poll_interval: float,
        timeout: float | None,
    ) -> dict[str, int]:
        """轮询 XYZ 反馈，连续多次稳定后视为机械回零完成。"""
        stable_samples = max(2, int(stable_samples))
        stable_tolerance = max(0, int(stable_tolerance))
        poll_interval = max(0.05, float(poll_interval))
        deadline = None if timeout is None else time.monotonic() + float(timeout)

        last_positions = None
        stable_count = 0
        while True:
            positions = self.get_all_positions()
            logger.info("机械回零等待中，当前位置=%s", positions)
            if last_positions is not None:
                stable = all(
                    abs(int(positions[axis]) - int(last_positions[axis])) <= stable_tolerance
                    for axis in ("x", "y", "z")
                )
                stable_count = stable_count + 1 if stable else 0
                if stable_count >= stable_samples:
                    return positions

            last_positions = positions
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("等待机械回零完成超时")
            time.sleep(poll_interval)

    def get_axis_position(self, axis: str) -> int:
        axis_key = self._normalize_axis(axis)
        addr = d_addr(AXIS_D_REGISTERS[axis_key]["position"])
        value = self.read_32bit_register(addr)
        self.last_position[axis_key] = value
        return value

    def get_all_positions(self) -> dict[str, int]:
        return {axis: self.get_axis_position(axis) for axis in ("x", "y", "z")}

    def request_current_position(self) -> MotionCommandResult:
        try:
            positions = self.get_all_positions()
        except Exception as exc:
            return MotionCommandResult(False, f"读取当前位置失败：{exc}")
        return MotionCommandResult(True, self._format_position(positions), positions)

    def move_axis_pulses(
        self,
        axis: str,
        move_pulses: int,
        speed: int,
        tolerance: int = 5,
        timeout: float = 20,
    ) -> MotionCommandResult:
        """按相对脉冲运动单轴，并用累计反馈位置判断到位。"""
        axis_key = self._normalize_axis(axis)
        move_pulses = int(move_pulses)
        speed = abs(int(speed))
        tolerance = max(0, abs(int(tolerance)))

        if not self.device_initialized:
            return MotionCommandResult(False, "设备尚未完成机械回零，禁止运动。")
        if move_pulses == 0:
            return MotionCommandResult(True, "运动脉冲为 0，未发送运动命令。", {"position": dict(self.last_position)})
        if speed <= 0:
            return MotionCommandResult(False, "速度必须大于 0。")

        try:
            with self._lock:
                axis_def = AXIS_D_REGISTERS[axis_key]
                axis_name = axis_def["name"]
                trigger_addr = d_addr(axis_def["trigger"])
                move_addr = d_addr(axis_def["move"])
                position_addr = d_addr(axis_def["position"])
                speed_addr = d_addr(axis_def["speed"])

                start_pos = self.read_32bit_register(position_addr)
                expected_pos = start_pos + move_pulses
                limit_error = self._check_soft_limit(axis_key, expected_pos)
                if limit_error:
                    return MotionCommandResult(False, limit_error, {"start_pos": start_pos, "expected_pos": expected_pos})

                logger.info(
                    "%s轴相对运动 start=%s move=%s expected=%s speed=%s tolerance=%s",
                    axis_name, start_pos, move_pulses, expected_pos, speed, tolerance,
                )
                self.write_32bit_register(move_addr, move_pulses)
                self.write_32bit_register(speed_addr, speed)
                self.write_32bit_register(trigger_addr, 1)
                time.sleep(0.05)
                self.write_32bit_register(trigger_addr, 0)

                start_time = time.monotonic()
                last_pos = None
                current_pos = start_pos
                while time.monotonic() - start_time <= float(timeout):
                    current_pos = self.read_32bit_register(position_addr)
                    self.last_position[axis_key] = current_pos
                    if abs(current_pos - expected_pos) <= tolerance:
                        positions = self.get_all_positions()
                        return MotionCommandResult(
                            True,
                            f"{axis_name}轴已到位，当前位置：{self._format_position(positions)}",
                            {"position": positions, "expected_pos": expected_pos},
                        )
                    if last_pos is not None:
                        if move_pulses > 0 and last_pos < expected_pos <= current_pos:
                            positions = self.get_all_positions()
                            return MotionCommandResult(
                                True,
                                f"{axis_name}轴已越过目标，按到位处理。当前位置：{self._format_position(positions)}",
                                {"position": positions, "expected_pos": expected_pos},
                            )
                        if move_pulses < 0 and last_pos > expected_pos >= current_pos:
                            positions = self.get_all_positions()
                            return MotionCommandResult(
                                True,
                                f"{axis_name}轴已越过目标，按到位处理。当前位置：{self._format_position(positions)}",
                                {"position": positions, "expected_pos": expected_pos},
                            )
                    last_pos = current_pos
                    time.sleep(0.1)
        except Exception as exc:
            logger.exception("%s轴运动失败", axis_key.upper())
            return MotionCommandResult(False, f"{axis_key.upper()}轴运动失败：{exc}")

        return MotionCommandResult(
            False,
            f"{axis_key.upper()}轴到位超时：目标={expected_pos}，当前位置={current_pos}",
            {"start_pos": start_pos, "expected_pos": expected_pos, "current_pos": current_pos},
        )

    def move_x_pulses(self, pulses, speed, tolerance=5, timeout=20):
        return self.move_axis_pulses("x", pulses, speed, tolerance, timeout)

    def move_y_pulses(self, pulses, speed, tolerance=5, timeout=20):
        return self.move_axis_pulses("y", pulses, speed, tolerance, timeout)

    def move_z_pulses(self, pulses, speed, tolerance=5, timeout=20):
        return self.move_axis_pulses("z", pulses, speed, tolerance, timeout)

    def move_axis_mm(self, axis: str, distance_mm: float, speed: int, tolerance=5, timeout=20):
        axis_key = self._normalize_axis(axis)
        pulses = int(float(distance_mm) * AXIS_PULSES_PER_MM[axis_key])
        return self.move_axis_pulses(axis_key, pulses, speed, tolerance, timeout)

    def move_axis(self, axis, distance_mm, speed: int | None = None):
        """兼容旧 UI 调用，按 mm 转换为相对脉冲。"""
        return self.move_axis_mm(axis, distance_mm, speed or 1000)

    def move_to_coordinate(self, x, y, z, speed: int | None = None):
        """按累计脉冲坐标顺序移动到指定 XYZ 位置。"""
        speed = int(speed or 1000)
        try:
            current = self.get_all_positions()
            targets = {"x": int(round(float(x))), "y": int(round(float(y))), "z": int(round(float(z)))}
        except Exception as exc:
            return MotionCommandResult(False, f"目标坐标非法或当前位置读取失败：{exc}")

        for axis in ("x", "y", "z"):
            delta = targets[axis] - current[axis]
            if delta == 0:
                continue
            result = self.move_axis_pulses(axis, delta, speed)
            if not result.success:
                return result
            current.update(result.data.get("position", {}))
        return MotionCommandResult(True, f"已移动到目标坐标：{self._format_position(current)}", current)

    def move_to_slot(self, tray_params: dict[str, Any], slot_index: int, speed: int):
        rows = int(tray_params["rows"])
        cols = int(tray_params["cols"])
        slot_index = int(slot_index)
        if slot_index < 0 or slot_index >= rows * cols:
            return MotionCommandResult(False, "槽位索引超出当前料盘范围。")
        row = slot_index // cols
        col = slot_index % cols
        target_x = int(tray_params["originX"]) + col * int(tray_params["pitchX"])
        target_y = int(tray_params["originY"]) + row * int(tray_params["pitchY"])
        target_z = int(tray_params.get("originZ") or 0)
        return self.move_to_coordinate(target_x, target_y, target_z, speed)

    def set_light_voltage(self, channel, voltage, timeout_sec=None):
        """光源控制协议未纳入本次 Modbus 运动控制，保留 UI 兼容入口。"""
        return MotionCommandResult(False, "当前 Modbus 运动控制器未接入光源调光协议。")

    def _check_soft_limit(self, axis: str, expected_pos: int) -> str | None:
        min_limit, max_limit = AXIS_SOFT_LIMITS[axis]
        logical_pos = expected_pos - int(self.home_position.get(axis, 0))
        if logical_pos < min_limit or logical_pos > max_limit:
            return (
                f"{axis.upper()}轴目标超出软限位：目标相对零点={logical_pos} 脉冲，"
                f"允许范围 {min_limit} ~ {max_limit}。"
            )
        return None

    @staticmethod
    def _normalize_axis(axis: str) -> str:
        axis_key = str(axis).lower()
        if axis_key not in AXIS_D_REGISTERS:
            raise ValueError(f"非法轴名称：{axis}")
        return axis_key

    @staticmethod
    def _format_position(position: dict[str, Any]) -> str:
        return "X={x}, Y={y}, Z={z}".format(
            x=int(position.get("x", 0)),
            y=int(position.get("y", 0)),
            z=int(position.get("z", 0)),
        )
