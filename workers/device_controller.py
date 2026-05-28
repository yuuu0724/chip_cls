"""兼容旧导入路径的 Modbus 运动控制器导出。

运动控制实现已经迁移到 :mod:`motion.modbus_motion_controller`，本文件只保留
旧代码中 ``from workers import DeviceController`` 的兼容名称。
"""

from motion import ModbusMotionController, MotionCommandResult

DeviceController = ModbusMotionController
DeviceCommandResult = MotionCommandResult

__all__ = ["DeviceController", "DeviceCommandResult"]
