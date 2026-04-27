"""数据持久化与服务容器模块。

包含：
- `ConfigManager`：应用级配置（图像目录、摄像头 ID 等）。
- `DataLogger`：每批检测的 CSV 日志 + 截图。
- `TrayManager`：料盘配置 CRUD。
- `AppServices`：把所有后端服务打包给 UI 层使用的容器。
"""
from .config_manager import ConfigManager
from .logger import DataLogger
from .services import AppServices
from .tray_manager import TrayManager

__all__ = ['AppServices', 'ConfigManager', 'DataLogger', 'TrayManager']
