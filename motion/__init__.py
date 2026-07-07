"""运动控制模块。"""

from .modbus_motion_controller import (
    AXIS_D_REGISTERS,
    AXIS_PULSES_PER_MM,
    AXIS_SOFT_LIMITS,
    MotionCommandResult,
    ModbusMotionController,
    combine_s32_from_words,
    d_addr,
    pulses_to_mm,
    split_s32_to_words,
    x_mm_to_pulses,
    y_mm_to_pulses,
    z_mm_to_pulses,
)

__all__ = [
    "AXIS_D_REGISTERS",
    "AXIS_PULSES_PER_MM",
    "AXIS_SOFT_LIMITS",
    "MotionCommandResult",
    "ModbusMotionController",
    "combine_s32_from_words",
    "d_addr",
    "pulses_to_mm",
    "split_s32_to_words",
    "x_mm_to_pulses",
    "y_mm_to_pulses",
    "z_mm_to_pulses",
]
