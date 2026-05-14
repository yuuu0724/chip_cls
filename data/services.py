"""应用服务容器（AppServices）。

设计目的
--------
历史上 `OCRApp.__init__` 里直接散装实例化了 5 个业务对象：

    self.engine            = OCREngine()
    self.logger            = DataLogger()
    self.template_manager  = TemplateManager()
    self.tray_manager      = TrayManager()
    self.config_manager    = ConfigManager()

这样有两个问题：
1. UI 直接依赖 5 个底层模块的具体类型，任何替换/测试/打桩都要改 UI；
2. 服务之间的隐式顺序（谁先建、谁能复用谁）散落在 UI 代码里，不易维护。

本模块把所有后端服务封装成 `AppServices` 数据类，UI 只依赖一个容器：

    services = AppServices.create_default()
    OCRApp(services)

后续若要替换 `OCREngine`（例如测试时换成桩对象），只需要在构造 `AppServices`
时注入，不需要改 UI。

兼容性
------
`OCRApp` 仍允许 `services=None`（默认会自己调 `create_default()`），所以老的
`OCRApp()` 零参数用法依然能跑，主入口 `main.py` 的改动是可选的。
"""

from __future__ import annotations

from dataclasses import dataclass

from ocr import OCREngine, TemplateManager
from workers import DeviceController

from .config_manager import ConfigManager
from .logger import DataLogger
from .tray_manager import TrayManager


@dataclass
class AppServices:
    """后端服务集合。

    字段说明
    --------
    engine : OCREngine
        OCR 推理引擎。进程内 ONNX session 走类级共享
        （`OCREngine._shared_*`），多次构造不会重复加载模型。
    template_manager : TemplateManager
        型号模板 CRUD；内部持有一个 `OCREngine`，与外层共享 session。
    tray_manager : TrayManager
        料盘配置读写（config/trays_config.json）。
    data_logger : DataLogger
        每批检测的 CSV 日志 + 批次完成后的界面截图。
    config_manager : ConfigManager
        应用级配置（图像目录、摄像头 ID 等）。
    """

    engine: OCREngine
    template_manager: TemplateManager
    tray_manager: TrayManager
    data_logger: DataLogger
    config_manager: ConfigManager
    device_controller: DeviceController | None = None

    @classmethod
    def create_default(cls) -> "AppServices":
        """按默认参数一次性构造所有服务。

        顺序要点
        --------
        先建 `OCREngine`，`TemplateManager` 内部即便再 new 一个 `OCREngine`
        也会命中共享 session（类级 `_shared_*`），只做轻量绑定，不会再次
        加载模型文件。

        Returns
        -------
        AppServices
            已装配好的容器，可直接传给主窗口。
        """
        engine = OCREngine()
        template_manager = TemplateManager()
        config_manager = ConfigManager()
        tray_manager = TrayManager()
        data_logger = DataLogger()
        device_controller = DeviceController(config_manager.get_config())
        return cls(
            engine=engine,
            template_manager=template_manager,
            tray_manager=tray_manager,
            data_logger=data_logger,
            config_manager=config_manager,
            device_controller=device_controller,
        )
