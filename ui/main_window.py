"""主应用窗口 - AI 芯片料盘视觉检测系统。

本模块只负责 UI 编排与事件粘合，所有业务服务（OCR 引擎、模板/料盘/配置
管理、日志）通过 `AppServices` 容器注入，方便替换与测试。

主要职责
--------
1. 构建左右两栏界面（左：料盘网格 + 顶部控制，右：摄像头/配置/任务控制）。
2. 响应用户操作：料盘切换、新增/删除料盘、上传参考图片、实时识别、刷新。
3. 管理后台线程：摄像头预览 `CameraWorker` + 实时识别 `LiveInspectionWorker`。
"""
import os
import sys
import time
import logging

import cv2
from motion import ConfigurableLightController, pulses_to_mm
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from data import AppServices
from workers import CameraWorker, ControlWorker, DeviceController, LiveInspectionWorker

from . import styles as S
from .dialogs import (
    AddTrayDialog,
    CameraCaptureDialog,
    TemplateConfirmDialog,
)
from .material_slot import MaterialSlot

logger = logging.getLogger(__name__)

DETECTION_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")


class MotionTaskWorker(QThread):
    """在后台执行单个运动控制任务，避免阻塞 Qt 主线程。"""

    finished = Signal(object)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self._task = task

    def run(self):
        try:
            result = self._task()
        except Exception as exc:
            from motion import MotionCommandResult

            result = MotionCommandResult(False, f"运动任务异常：{exc}")
        self.finished.emit(result)


class ChipPreviewWorker(QThread):
    """后台跑芯片检测，避免摄像头预览线程被 ONNX 推理卡住。"""

    result_ready = Signal(object)

    def __init__(self, engine, frame_bgr, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.frame_bgr = frame_bgr

    def run(self):
        try:
            result = self.engine.detect_chip_preview(self.frame_bgr)
        except Exception as exc:
            h, w = self.frame_bgr.shape[:2] if self.frame_bgr is not None else (0, 0)
            result = {
                "chips": [],
                "selected_index": -1,
                "image_shape": [int(h), int(w)],
                "status": f"error: {exc}",
            }
        self.result_ready.emit(result)


class OCRApp(QMainWindow):
    """主窗口。

    Parameters
    ----------
    services : AppServices | None
        后端服务容器。传 ``None`` 时会调用 ``AppServices.create_default()``
        按默认配置构造全套服务——这样 `OCRApp()` 零参数的老用法仍然可用。

    重要成员
    --------
    self.services : AppServices
        5 个后端服务的统一入口（engine / template_manager / tray_manager /
        data_logger / config_manager）。
    self.camera_worker : CameraWorker | None
        摄像头预览线程；在 `start_camera_preview` 中懒启动。
    self.slots : list[MaterialSlot]
        当前料盘展开的全部槽位组件，顺序与料位编号一致。
    """

    def __init__(self, services: AppServices | None = None):
        super().__init__()
        self.setWindowTitle("AI 芯片料盘视觉检测系统")
        self.setStyleSheet(S.MAIN_WINDOW)
        self._configure_responsive_metrics()

        # 服务容器：UI 只依赖这一个对象，解耦具体实现
        self.services = services if services is not None else AppServices.create_default()
        if getattr(self.services, "device_controller", None) is None:
            self.services.device_controller = DeviceController(
                self.services.config_manager.get_config()
            )
        self.device_controller = self.services.device_controller
        if getattr(self.services, "light_controller", None) is None:
            self.services.light_controller = ConfigurableLightController(
                motion_controller=self.device_controller,
                config=self.services.config_manager.get_config(),
            )
        self.light_controller = self.services.light_controller

        # UI 相关状态
        self.camera_worker = None   # 摄像头预览线程（懒启动）
        self.live_worker = None     # 正在运行的实时识别线程（None 表示空闲）
        self.motion_worker = None   # 正在运行的运动控制线程
        self.chip_preview_worker = None
        self.debug_worker = None
        self.slots = []             # 当前料盘的槽位组件列表
        self.chip_preview_result = None
        self.chip_preview_last_started = 0.0
        self.chip_preview_interval_seconds = 0.35
        app_config = self.services.config_manager.get_config()
        self.origin_center_tolerance_mm = float(app_config.get("origin_center_tolerance_mm", 2.0))
        self.origin_center_tolerance_px = float(app_config.get("origin_center_tolerance_px", 15.0))
        self.origin_center_x_pulses_per_px = float(app_config.get("origin_center_x_pulses_per_px", 25.0))
        self.origin_center_y_pulses_per_px = float(app_config.get("origin_center_y_pulses_per_px", 12.5))
        self._active_task_mode = None
        self._task_stop_requested = False
        self._task_paused = False
        self._current_slot_order = []
        self._pending_return_to_first_slot = False
        self._return_to_first_slot_callback = None
        self._skip_next_tray_origin_move = False
        self._red_light_flash_on = False
        self.red_light_timer = QTimer(self)
        self.red_light_timer.timeout.connect(self._toggle_red_light_flash)
        self._set_red_light(False)

        self.init_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.centralWidget().setEnabled(False)

        QTimer.singleShot(0, self.ensure_startup_motion_ready)

    def _configure_responsive_metrics(self):
        """根据当前显示器分辨率计算主界面尺寸参数。"""
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            screen_w = max(1, available.width())
            screen_h = max(1, available.height())
        else:
            screen_w, screen_h = 1024, 768

        self._ui_scale = max(0.85, min(1.25, min(screen_w / 1024.0, screen_h / 768.0)))
        self._main_margin = max(6, int(10 * self._ui_scale))
        self._main_spacing = max(6, int(10 * self._ui_scale))
        self._right_panel_width = max(280, min(380, int(screen_w * 0.30)))
        self._grid_spacing = max(3, int(5 * self._ui_scale))
        self._slot_min_size = max(28, int(32 * self._ui_scale))
        self._slot_max_size = max(58, int(76 * self._ui_scale))
        self._grid_rows = 1
        self._grid_cols = 1

    def _build_status_color_legend(self):
        """构造槽位颜色含义提示。"""
        legend = QWidget()
        layout = QHBoxLayout(legend)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel("颜色:")
        title.setStyleSheet(S.COLOR_LEGEND_TITLE)
        layout.addWidget(title)
        layout.addWidget(self._build_status_legend_item("未处理", S.COLOR_LEGEND_SWATCH_DEFAULT))
        layout.addWidget(self._build_status_legend_item("正确", S.COLOR_LEGEND_SWATCH_GREEN))
        layout.addWidget(self._build_status_legend_item("异常", S.COLOR_LEGEND_SWATCH_RED))
        return legend

    def _build_status_legend_item(self, text, swatch_style):
        item = QWidget()
        layout = QHBoxLayout(item)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        swatch = QFrame()
        swatch.setFixedSize(14, 14)
        swatch.setStyleSheet(swatch_style)
        layout.addWidget(swatch)

        label = QLabel(text)
        label.setStyleSheet(S.COLOR_LEGEND_TEXT)
        layout.addWidget(label)
        return item

    def init_ui(self):
        """构建主窗口 UI：左（料盘网格+顶部控制）+ 右（摄像头/配置/任务）。

        方法体较长但结构扁平，按视觉分区线性组织：
        - 顶部控制区：料盘选择、新增/删除、型号/角度显示
        - 中部料位网格：根据当前料盘规格动态生成
        - 右侧三分区：摄像头预览、配置中心、任务控制
        """
        central = QWidget()
        central.setStyleSheet("background-color: #1a1f2e;")
        self.setCentralWidget(central)

        # 主布局：左右二栏，左侧占 75% 宽
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(
            self._main_margin,
            self._main_margin,
            self._main_margin,
            self._main_margin,
        )
        main_layout.setSpacing(self._main_spacing)

        # ========== 左侧：料位网格 + 顶部控制 ==========
        left_panel = QWidget()
        left_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(8)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # --- 顶部料盘选择和参数区 ---
        top_control_widget = QWidget()
        top_control_layout = QVBoxLayout(top_control_widget)
        top_control_layout.setContentsMargins(0, 0, 0, 0)
        top_control_layout.setSpacing(4)

        tray_control_row = QHBoxLayout()
        tray_control_row.setSpacing(8)

        info_control_row = QHBoxLayout()
        info_control_row.setSpacing(8)

        # 料盘选择下拉
        tray_label = QLabel("料盘:")
        tray_label.setStyleSheet(S.LABEL_TITLE)
        tray_control_row.addWidget(tray_label)

        self.tray_combo = QComboBox()
        self.tray_combo.setMaximumWidth(140)
        self.tray_combo.setMinimumHeight(42)
        self.tray_combo.setStyleSheet(S.TRAY_COMBO)

        # 从 tray_manager 拉取全部料盘写入下拉（名称本身作为唯一 key）
        for tray_id in self.services.tray_manager.get_tray_list():
            tray_info = self.services.tray_manager.get_tray_info(tray_id) or {}
            self.tray_combo.addItem(tray_info.get("name") or tray_id, tray_id)

        self.tray_combo.currentIndexChanged.connect(self.on_tray_changed)
        tray_control_row.addWidget(self.tray_combo)

        # 新增料盘按钮
        add_tray_btn = QPushButton("＋ 新增料盘")
        add_tray_btn.setMinimumHeight(42)
        add_tray_btn.setStyleSheet(S.ADD_TRAY_BTN)
        add_tray_btn.clicked.connect(self.add_new_tray)
        tray_control_row.addWidget(add_tray_btn)

        edit_tray_btn = QPushButton("编辑料盘")
        edit_tray_btn.setMinimumHeight(42)
        edit_tray_btn.setStyleSheet(S.ADD_TRAY_BTN)
        edit_tray_btn.clicked.connect(self.edit_current_tray)
        tray_control_row.addWidget(edit_tray_btn)

        # 删除料盘按钮（红色警示色）
        delete_tray_btn = QPushButton("－ 删除料盘")
        delete_tray_btn.setMinimumHeight(42)
        delete_tray_btn.setStyleSheet(S.DELETE_TRAY_BTN)
        delete_tray_btn.clicked.connect(self.delete_current_tray)
        tray_control_row.addWidget(delete_tray_btn)

        tray_control_row.addStretch()

        # 型号实时显示（只读文字）
        model_label = QLabel("型号:")
        model_label.setStyleSheet(S.LABEL_TITLE)
        info_control_row.addWidget(model_label)

        self.model_display = QLabel("ATMLH904")
        self.model_display.setStyleSheet(S.VALUE_HIGHLIGHT)
        self.model_display.setMinimumWidth(140)
        info_control_row.addWidget(self.model_display)

        # 角度实时显示（只读文字）
        angle_label = QLabel("角度:")
        angle_label.setStyleSheet(S.LABEL_TITLE)
        info_control_row.addWidget(angle_label)

        self.angle_display = QLabel("90°")
        self.angle_display.setStyleSheet(S.VALUE_HIGHLIGHT)
        self.angle_display.setMinimumWidth(60)
        info_control_row.addWidget(self.angle_display)

        info_control_row.addSpacing(12)
        info_control_row.addWidget(self._build_status_color_legend())
        info_control_row.addStretch()

        top_control_layout.addLayout(tray_control_row)
        top_control_layout.addLayout(info_control_row)
        left_layout.addWidget(top_control_widget)
        
        # --- 料位网格区（根据料盘规格动态生成） ---
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.grid_scroll.setStyleSheet("QScrollArea { border: none; background-color: #1a1f2e; }")

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background-color: #1a1f2e;")
        self.grid_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(self._grid_spacing)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grid_scroll.setWidget(self.grid_container)

        # 首次用当前选中料盘的规格铺网格
        first_tray_id = self.tray_combo.currentData()
        rows, cols = (
            self.services.tray_manager.get_tray_dimensions(first_tray_id)
            if first_tray_id else (3, 7)
        )
        self._rebuild_grid(rows, cols)

        left_layout.addWidget(self.grid_scroll, 1)

        # ========== 右侧：摄像头 + 配置中心 + 任务控制 ==========
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        right_scroll.setFixedWidth(self._right_panel_width)
        right_scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")

        right_panel = QWidget()
        right_panel.setMinimumWidth(self._right_panel_width - 18)
        right_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(5)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # ---------- 区域 1：摄像头预览 ----------
        camera_section = QFrame()
        camera_section.setStyleSheet(
            "QFrame { background-color: transparent; border: none; "
            "border-radius: 8px; padding: 0px; }"
        )
        camera_section_layout = QVBoxLayout(camera_section)
        camera_section_layout.setSpacing(0)
        camera_section_layout.setContentsMargins(0, 0, 0, 0)

        # 摄像头 label：启动前占位显示"无摄像头信号"，启动后在 update_camera_frame 中更新 pixmap
        self.camera_frame = QLabel()
        self.camera_frame.setScaledContents(False)
        self.camera_frame.setStyleSheet(S.CAMERA_FRAME)
        self.camera_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_frame.setText("无摄像头信号")
        camera_section_layout.addWidget(self.camera_frame, 1)

        # 启动预览线程（异步打开摄像头，失败时 label 保持占位文字）
        self.start_camera_preview()

        right_layout.addWidget(camera_section, 3)
        
        # ---------- 区域 2：配置中心 ----------
        param_section = QFrame()
        param_section.setStyleSheet(S.SECTION_FRAME)
        param_section_layout = QVBoxLayout(param_section)
        param_section_layout.setSpacing(5)
        param_section_layout.setContentsMargins(6, 6, 6, 6)

        # 分区标题
        param_title = QLabel("配置中心")
        param_title.setStyleSheet(S.SECTION_TITLE)
        param_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        param_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        param_section_layout.addWidget(param_title)

        # 上传参考图片（本地文件 / 摄像头拍摄，二选一）
        upload_btn = QPushButton("上传参考图片")
        upload_btn.setMinimumHeight(38)
        upload_btn.setStyleSheet(S.WARNING_BUTTON)
        upload_btn.clicked.connect(self.upload_reference_image)
        param_section_layout.addWidget(upload_btn)

        # 型号编辑行：label + 输入框，供用户手动覆写当前批次的目标型号
        model_edit_layout = QHBoxLayout()
        model_edit_layout.setSpacing(5)
        model_label = QLabel("型号:")
        model_label.setStyleSheet(S.PARAM_LABEL)
        model_edit_layout.addWidget(model_label)

        self.model_input = QLineEdit("ATMLH904")
        self.model_input.setMinimumHeight(32)
        self.model_input.setStyleSheet(S.PARAM_INPUT)
        model_edit_layout.addWidget(self.model_input)
        param_section_layout.addLayout(model_edit_layout)

        # 角度编辑行：同上，目标角度
        angle_edit_layout = QHBoxLayout()
        angle_edit_layout.setSpacing(5)
        angle_label = QLabel("角度:")
        angle_label.setStyleSheet(S.PARAM_LABEL)
        angle_edit_layout.addWidget(angle_label)

        self.angle_input = QLineEdit("90")
        self.angle_input.setMinimumHeight(32)
        self.angle_input.setStyleSheet(S.PARAM_INPUT)
        angle_edit_layout.addWidget(self.angle_input)
        param_section_layout.addLayout(angle_edit_layout)

        speeds = self._motion_speeds()
        self.motion_speed_spins = {}
        for axis in ("x", "y", "z"):
            speed_edit_layout = QHBoxLayout()
            speed_edit_layout.setSpacing(5)
            speed_label = QLabel(f"{axis.upper()}速度:")
            speed_label.setStyleSheet(S.PARAM_LABEL)
            speed_edit_layout.addWidget(speed_label)

            speed_spin = QSpinBox()
            speed_spin.setRange(1, 999999)
            speed_spin.setValue(speeds[axis])
            speed_spin.setSingleStep(100)
            speed_spin.setSuffix(" pulse/s")
            speed_spin.setMinimumHeight(32)
            speed_spin.setStyleSheet(
                """
                QSpinBox {
                    color: #ffffff;
                    background-color: #2a2a2e;
                    border: 1px solid #007AFF;
                    border-radius: 8px;
                    padding: 5px 9px;
                    font-size: 13px;
                    font-weight: 500;
                    selection-background-color: #007AFF;
                }
                QSpinBox:focus {
                    border: 2px solid #007AFF;
                    padding: 4px 8px;
                }
                """
            )
            speed_spin.valueChanged.connect(
                lambda value, axis_key=axis: self._save_axis_motion_speed_config(axis_key, value)
            )
            self.motion_speed_spins[axis] = speed_spin
            speed_edit_layout.addWidget(speed_spin)
            param_section_layout.addLayout(speed_edit_layout)

        right_layout.addWidget(param_section, 2)
        
        # ---------- 区域 3：任务控制 ----------
        button_section = QFrame()
        button_section.setStyleSheet(S.SECTION_FRAME)
        button_section_layout = QVBoxLayout(button_section)
        button_section_layout.setSpacing(5)
        button_section_layout.setContentsMargins(6, 6, 6, 6)

        button_title = QLabel("任务控制")
        button_title.setStyleSheet(S.SECTION_TITLE)
        button_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        button_section_layout.addWidget(button_title)

        # 启动检测：整个界面最醒目的入口
        self.start_btn = QPushButton("开始检测")
        self.start_btn.setMinimumHeight(50)
        self.start_btn.setStyleSheet(S.START_BUTTON)
        self.start_btn.clicked.connect(self.start_live_inspection)
        button_section_layout.addWidget(self.start_btn)

        run_control_row = QHBoxLayout()
        run_control_row.setSpacing(5)

        self.pause_btn = QPushButton("暂停检测")
        self.pause_btn.setMinimumHeight(36)
        self.pause_btn.setStyleSheet(S.PAUSE_BUTTON)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.toggle_pause_current_task)
        run_control_row.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("结束检测")
        self.stop_btn.setMinimumHeight(36)
        self.stop_btn.setStyleSheet(S.STOP_BUTTON)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_current_task)
        run_control_row.addWidget(self.stop_btn)

        button_section_layout.addLayout(run_control_row)

        self.debug_btn = QPushButton("调试检测")
        self.debug_btn.setMinimumHeight(34)
        self.debug_btn.setStyleSheet(S.DEBUG_BUTTON)
        self.debug_btn.clicked.connect(self.start_debug_inspection)
        button_section_layout.addWidget(self.debug_btn)

        # 刷新：重置所有槽位到"待机"
        bottom_action_row = QHBoxLayout()
        bottom_action_row.setSpacing(5)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setMinimumHeight(34)
        refresh_btn.setStyleSheet(S.REFRESH_BUTTON)
        refresh_btn.clicked.connect(self.refresh_templates)
        bottom_action_row.addWidget(refresh_btn)

        # 访问历史数据：CSV 与截图的文件选择器
        history_btn = QPushButton("访问历史数据")
        history_btn.setMinimumHeight(34)
        history_btn.setStyleSheet(S.HISTORY_BUTTON)
        history_btn.clicked.connect(self.open_history_data)
        bottom_action_row.addWidget(history_btn)

        # 退出：同 closeEvent，清理摄像头线程
        exit_btn = QPushButton("退出")
        exit_btn.setMinimumHeight(34)
        exit_btn.setStyleSheet(S.EXIT_BUTTON)
        exit_btn.clicked.connect(self.close)
        bottom_action_row.addWidget(exit_btn)
        button_section_layout.addLayout(bottom_action_row)

        right_layout.addWidget(button_section, 2)
        right_scroll.setWidget(right_panel)

        # 左侧料盘自适应缩放，右侧操作区保持完整可见。
        main_layout.addWidget(left_panel, 1)
        main_layout.addWidget(right_scroll, 0)

        # 工业屏直接全屏，避免标题栏占用可视区。
        self.showFullScreen()
        QTimer.singleShot(0, self._update_slot_sizes)

    def on_tray_changed(self):
        """料盘切换事件。

        副作用
        ------
        - 更新顶部型号/角度的只读显示
        - 同步写入右侧"配置中心"的可编辑输入框
        - 按新料盘的规格重建料位网格（槽位数量可能变化）
        """
        tray_id = self.tray_combo.currentData()
        if not tray_id:
            return

        model, angle = self.services.tray_manager.get_tray_model_and_angle(tray_id)
        self.model_display.setText(model or "")
        self.model_input.setText(model or "")
        self.angle_display.setText(f"{angle}°" if angle else "0°")
        self.angle_input.setText(str(angle) if angle else "0")

        rows, cols = self.services.tray_manager.get_tray_dimensions(tray_id)
        self._rebuild_grid(rows, cols)
        if self._skip_next_tray_origin_move:
            self._skip_next_tray_origin_move = False
            return
        self._move_to_current_tray_origin()

    def _move_to_current_tray_origin(self):
        """切换料盘后自动移动到该料盘首槽原点。"""
        if not self.device_controller.is_initialized:
            return
        if self.motion_worker is not None and self.motion_worker.isRunning():
            QMessageBox.warning(self, "提示", "运动任务进行中，已跳过本次料盘原点移动。")
            return

        tray_id = self.tray_combo.currentData()
        try:
            origin_coord = self.services.tray_manager.calculate_slot_coordinate(tray_id, 0)
        except ValueError as exc:
            QMessageBox.warning(self, "料盘原点不可用", str(exc))
            return

        self._run_motion_task(
            f"正在移动到料盘 {tray_id} 原点...",
            lambda: self._move_to_coordinate(origin_coord["x"], origin_coord["y"], origin_coord["z"]),
            show_success=False,
        )

    def _rebuild_grid(self, rows, cols):
        """按新的 `(rows, cols)` 重建料位网格。

        会释放原有 `MaterialSlot` 组件并重新创建，内部索引从 1 开始
        （CSV/UI 显示时也以 1 基准）。
        """
        # 先把旧组件从布局里摘掉并安排销毁（deleteLater 避免正在响应事件的组件被立即析构）
        for slot in self.slots:
            self.grid_layout.removeWidget(slot)
            slot.deleteLater()
        self.slots.clear()

        self._grid_rows = max(1, int(rows))
        self._grid_cols = max(1, int(cols))
        total = self._grid_rows * self._grid_cols
        for i in range(total):
            slot = MaterialSlot(i + 1, display_index=self._display_slot_number(i))
            slot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            ui_row = i // self._grid_cols
            ui_col = i % self._grid_cols
            grid_row = self._grid_rows - 1 - ui_row
            self.grid_layout.addWidget(slot, grid_row, ui_col)
            self.slots.append(slot)
        QTimer.singleShot(0, self._update_slot_sizes)

    def _display_slot_number(self, slot_index):
        """按自然顺序显示槽位编号：第1行第1个为 1。"""
        try:
            index = int(slot_index)
        except (TypeError, ValueError):
            return 1
        return index + 1

    def _update_slot_sizes(self):
        """根据料盘区域可用空间自适应计算槽位尺寸。

        10x10 是 1024x768 工业屏的保底完整显示目标；更大规格低于可读
        尺寸时启用滚动兜底，保证右侧操作面板不被挤压。
        """
        if not self.slots or not hasattr(self, "grid_scroll"):
            return

        rows = max(1, self._grid_rows)
        cols = max(1, self._grid_cols)
        viewport = self.grid_scroll.viewport().size()
        available_w = max(1, viewport.width() - 2)
        available_h = max(1, viewport.height() - 2)
        spacing = self._grid_spacing

        slot_by_w = (available_w - spacing * (cols - 1)) / cols
        slot_by_h = (available_h - spacing * (rows - 1)) / rows
        slot_size = int(min(slot_by_w, slot_by_h, self._slot_max_size))
        slot_size = max(self._slot_min_size, slot_size)

        grid_w = cols * slot_size + spacing * (cols - 1)
        grid_h = rows * slot_size + spacing * (rows - 1)
        self.grid_container.setMinimumSize(grid_w, grid_h)

        for slot in self.slots:
            slot.setFixedSize(slot_size, slot_size)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_slot_sizes)

    def add_new_tray(self):
        """弹出"新增料盘"对话框并把结果写回 tray_manager。

        用户点击确认后：
        1. 以用户输入的料盘名称作为唯一 key 写入配置
        2. 追加到下拉末尾并切换过去（只重建网格，不移动设备）
        """
        if self.motion_worker is not None and self.motion_worker.isRunning():
            QMessageBox.warning(self, "提示", "运动任务进行中，请等待完成后再新增料盘。")
            return

        self._open_add_tray_dialog()

    def _open_add_tray_dialog(self):
        """打开新增料盘弹窗，使用当前坐标作为新料盘原点。"""
        dialog = AddTrayDialog(
            self.services.tray_manager.get_tray_list(),
            coordinate_provider=self._read_current_position_for_dialog,
            center_status_provider=self.get_origin_center_status,
            jog_speed=self._motion_speeds(),
            parent=self,
        )
        dialog.jog_requested.connect(lambda axis, pulses, speed: self._jog_axis_for_tray_dialog(dialog, axis, pulses, speed))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        tray_data = dialog.get_tray_data()
        tray_id = tray_data["tray_id"]
        tray_name = tray_data["name"]
        spec_key = tray_data["spec"]
        self.services.tray_manager.add_tray(
            tray_id,
            name=tray_name,
            description="",
            model="",
            angle=0,
            spec=spec_key,
            rows=tray_data["rows"],
            cols=tray_data["cols"],
            pitch_x=tray_data["pitch_x"],
            pitch_y=tray_data["pitch_y"],
            pitch_unit=tray_data.get("pitch_unit"),
            origin_x=tray_data["origin_x"],
            origin_y=tray_data["origin_y"],
            origin_z=tray_data["origin_z"],
        )

        self._skip_next_tray_origin_move = True
        self.tray_combo.addItem(tray_name, tray_id)
        self.tray_combo.setCurrentIndex(self.tray_combo.count() - 1)
        QMessageBox.information(self, "新增成功", f"料盘 {tray_name} 已新增。")

    def edit_current_tray(self):
        tray_id = self.tray_combo.currentData()
        if not tray_id:
            QMessageBox.warning(self, "提示", "当前没有选中的料盘。")
            return
        tray_info = self.services.tray_manager.get_tray_info(tray_id)
        if not tray_info:
            QMessageBox.warning(self, "提示", "当前料盘配置不存在。")
            return

        dialog = AddTrayDialog(
            self.services.tray_manager.get_tray_list(),
            coordinate_provider=self._read_current_position_for_dialog,
            center_status_provider=self.get_origin_center_status,
            jog_speed=self._motion_speeds(),
            parent=self,
            initial_data={"tray_id": tray_id, **tray_info},
            edit_mode=True,
        )
        dialog.jog_requested.connect(lambda axis, pulses, speed: self._jog_axis_for_tray_dialog(dialog, axis, pulses, speed))
        dialog.origin_saved.connect(lambda position: self._save_tray_origin(tray_id, position))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        tray_data = dialog.get_tray_data()
        new_tray_id = tray_data["tray_id"]
        if new_tray_id != tray_id:
            if not self.services.tray_manager.rename_tray(tray_id, new_tray_id):
                QMessageBox.warning(self, "保存失败", f"料盘编号 {new_tray_id} 已存在或无效。")
                return
            tray_id = new_tray_id

        self.services.tray_manager.update_tray(
            tray_id,
            name=new_tray_id,
            spec=tray_data["spec"],
            rows=tray_data["rows"],
            cols=tray_data["cols"],
            pitchX=tray_data["pitch_x"],
            pitchY=tray_data["pitch_y"],
            pitchUnit=tray_data.get("pitch_unit"),
            firstSlotOrigin={
                "x": tray_data["origin_x"],
                "y": tray_data["origin_y"],
                "z": tray_data["origin_z"],
            },
        )
        index = self.tray_combo.currentIndex()
        self.tray_combo.setItemText(index, new_tray_id)
        self.tray_combo.setItemData(index, new_tray_id)
        self._skip_next_tray_origin_move = True
        self.on_tray_changed()
        QMessageBox.information(self, "保存成功", f"料盘 {new_tray_id} 已更新。")

    def delete_current_tray(self):
        """删除当前下拉里选中的料盘。

        拒绝条件（依次检查）：
        - 没有选中任何料盘
        - 下拉里仅剩 1 项（至少要留一个）
        - 正在运行实时识别任务
        - 二次确认被用户取消
        - 底层 `tray_manager.delete_tray` 拒绝删除（如默认 A0001 受保护）
        """
        tray_id = self.tray_combo.currentData()
        if not tray_id:
            QMessageBox.warning(self, "提示", "当前没有选中的料盘。")
            return
        tray_info = self.services.tray_manager.get_tray_info(tray_id) or {}
        tray_name = tray_info.get("name") or tray_id

        if self.tray_combo.count() <= 1:
            QMessageBox.warning(self, "提示", "至少保留一个料盘，不能全部删除。")
            return

        if (self.live_worker is not None and self.live_worker.isRunning()) or \
                (self.debug_worker is not None and self.debug_worker.isRunning()):
            QMessageBox.warning(self, "提示", "检测任务进行中，请结束后再删除。")
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除料盘 {tray_name} 吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if not self.services.tray_manager.delete_tray(tray_id):
            QMessageBox.warning(
                self, "删除失败",
                f"料盘 {tray_name} 不允许删除（默认料盘受保护）。",
            )
            return

        # 从下拉里移除当前项；Qt 会自动选中相邻项并触发 on_tray_changed 重建网格
        index = self.tray_combo.currentIndex()
        self.tray_combo.removeItem(index)

    def ensure_startup_motion_ready(self):
        """软件启动后连接 Modbus RTU，并由用户决定是否机械复位。"""
        self._ensure_tray_selected_on_entry()
        reply = QMessageBox.question(
            self,
            "机械复位",
            "是否现在执行机械复位？\n\n"
            "选择“否”将只连接控制器并进入软件，后续运动操作仍需先完成复位。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.No:
            self._run_motion_task(
                "正在连接控制器...",
                lambda: self._connect_only(),
                on_success=self._on_motion_ready,
                on_failure=self._on_startup_connect_failed,
                show_success=False,
            )
            return

        self._run_motion_task(
            "正在连接控制器并等待机械回零完成...",
            lambda: self._connect_then_home(),
            on_success=self._on_motion_ready,
            on_failure=self._on_startup_motion_failed,
            show_success=False,
        )

    def _connect_only(self):
        port = getattr(self.device_controller, "port", "COM14")
        return self.device_controller.connect(port)

    def _connect_then_home(self):
        port = getattr(self.device_controller, "port", "COM14")
        result = self.device_controller.connect(port)
        if not result.success:
            return result
        return self.device_controller.home()

    def _on_motion_ready(self, result):
        if self.light_controller is not None:
            self.light_controller.reload_config(self.services.config_manager.get_config())
            try:
                self.light_controller.apply_initial_state()
            except Exception:
                logger.exception("应用灯光初始状态失败")
        self.centralWidget().setEnabled(True)
        self.statusBar().showMessage(result.message, 3000)

    def _on_startup_motion_failed(self, result):
        retry = QMessageBox.warning(
            self,
            "回零未完成",
            f"{result.message}\n\n软件会保持锁定，请排查控制器、串口和限位状态后重试。",
            QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Close,
            QMessageBox.StandardButton.Retry,
        )
        if retry == QMessageBox.StandardButton.Retry:
            QTimer.singleShot(0, self.ensure_startup_motion_ready)
        else:
            self.close()

    def _on_startup_connect_failed(self, result):
        reply = QMessageBox.warning(
            self,
            "控制器未连接",
            f"{result.message}\n\n是否重试连接？选择“否”将进入软件，但运动控制不可用。",
            QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Close,
            QMessageBox.StandardButton.Retry,
        )
        if reply == QMessageBox.StandardButton.Retry:
            QTimer.singleShot(0, self.ensure_startup_motion_ready)
        elif reply == QMessageBox.StandardButton.No:
            self.centralWidget().setEnabled(True)
            self.statusBar().showMessage("控制器未连接，运动控制不可用。", 5000)
        else:
            self.close()

    def _ensure_tray_selected_on_entry(self):
        """进入操作界面后确保至少选中一个料盘。"""
        if self.tray_combo.count() <= 0 or not self.tray_combo.currentData():
            QMessageBox.information(self, "请选择料盘", "请先选择已有料盘，或新增一个料盘。")

    def _validate_before_operation(self, require_motion_params=False, require_motion_ready=False):
        """统一校验料盘选择、运动复位状态和运动参数。"""
        tray_id = self.tray_combo.currentData()
        if not tray_id:
            QMessageBox.warning(self, "提示", "请先选择或新增料盘。")
            return False

        if require_motion_ready or require_motion_params:
            if not self.device_controller.is_initialized:
                QMessageBox.warning(self, "禁止操作", "设备尚未复位，禁止执行运动操作。")
                return False

        if require_motion_params:
            ok, message = self.services.tray_manager.is_tray_config_complete(tray_id)
            if not ok:
                QMessageBox.warning(self, "料盘参数不完整", message)
                return False
        return True

    def _run_motion_task(
        self,
        title,
        task,
        on_success=None,
        on_failure=None,
        show_success=True,
        modal=True,
    ):
        """在后台线程执行运动控制任务，并把结果回到主线程处理。"""
        if self.motion_worker is not None and self.motion_worker.isRunning():
            QMessageBox.warning(self, "运动任务进行中", "请等待当前运动任务完成后再操作。")
            return

        progress = QProgressDialog(title, "", 0, 0, self)
        progress.setWindowTitle("运动控制")
        progress.setCancelButton(None)
        progress.setWindowModality(
            Qt.WindowModality.ApplicationModal if modal else Qt.WindowModality.NonModal
        )
        progress.show()

        worker = MotionTaskWorker(task, self)
        self.motion_worker = worker

        def handle_finished(result):
            progress.close()
            self.motion_worker = None
            if result.success:
                if on_success is not None:
                    on_success(result)
                if show_success:
                    self.statusBar().showMessage(result.message, 3000)
            else:
                if on_failure is not None:
                    on_failure(result)
                else:
                    QMessageBox.warning(self, "运动失败", result.message)
            if self._pending_return_to_first_slot:
                self._start_return_to_first_slot()

        worker.finished.connect(handle_finished)
        worker.start()

    def _motion_speed(self):
        return self._motion_speeds()["x"]

    def _motion_speeds(self):
        config = self.services.config_manager.get_config()
        try:
            fallback_speed = int(config.get("motion_speed", 1000))
        except (TypeError, ValueError):
            fallback_speed = 1000

        speeds = {}
        for axis in ("x", "y", "z"):
            try:
                speed = int(config.get(f"motion_speed_{axis}", fallback_speed))
            except (TypeError, ValueError):
                speed = fallback_speed
            speeds[axis] = max(1, speed)
        return speeds

    def _move_to_coordinate(self, x, y, z):
        return self.device_controller.move_to_coordinate_with_axis_speeds(
            x,
            y,
            z,
            self._motion_speeds(),
        )

    def _save_motion_speed_config(self, value):
        speed = max(1, int(value))
        self.services.config_manager.set_motion_config(motion_speed=speed)

    def _save_axis_motion_speed_config(self, axis, value):
        axis_key = str(axis).lower()
        if axis_key not in {"x", "y", "z"}:
            return
        speed = max(1, int(value))
        self.services.config_manager.set_motion_config(**{f"motion_speed_{axis_key}": speed})

    def _red_light_flash_interval_ms(self):
        config = self.services.config_manager.get_config()
        try:
            interval = int(config.get("red_light_flash_interval_ms", 500))
        except (TypeError, ValueError):
            interval = 500
        return max(100, interval)

    def _set_red_light(self, on):
        if self.light_controller is None:
            return
        try:
            self.light_controller.set_red_light(bool(on))
        except Exception:
            logger.exception("设置红灯状态失败")

    def _start_red_light_flash(self):
        if self.light_controller is not None:
            self.light_controller.reload_config(self.services.config_manager.get_config())
        self._red_light_flash_on = False
        self._set_red_light(False)
        self.red_light_timer.start(self._red_light_flash_interval_ms())

    def _stop_red_light_flash(self):
        self.red_light_timer.stop()
        self._red_light_flash_on = False
        self._set_red_light(False)

    def _toggle_red_light_flash(self):
        self._red_light_flash_on = not self._red_light_flash_on
        self._set_red_light(self._red_light_flash_on)

    def _set_task_controls_running(self, pause_enabled=True):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.pause_btn.setEnabled(pause_enabled)
        self.pause_btn.setText("继续检测" if self._task_paused else "暂停检测")
        if hasattr(self, "debug_btn"):
            self.debug_btn.setEnabled(False)

    def _set_task_controls_idle(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("暂停检测")
        self._task_paused = False
        if hasattr(self, "debug_btn"):
            self.debug_btn.setEnabled(not self._is_debug_running())

    def _is_live_running(self):
        return self.live_worker is not None and self.live_worker.isRunning()

    def _is_debug_running(self):
        return self.debug_worker is not None and self.debug_worker.isRunning()

    @staticmethod
    def _build_natural_slot_order(rows, cols):
        """生成从第1行到最后一行、每行从左到右的 0 基准槽位顺序。"""
        rows = int(rows)
        cols = int(cols)
        return list(range(max(0, rows * cols)))

    def _first_detection_slot_index(self):
        if self._current_slot_order:
            return self._current_slot_order[0]
        return 0

    @staticmethod
    def _format_axis_mm(axis, pulses):
        return f"{pulses_to_mm(axis, pulses):.3f} mm"

    @classmethod
    def _format_position_mm(cls, position):
        return (
            f"X={cls._format_axis_mm('x', position.get('x', 0))}, "
            f"Y={cls._format_axis_mm('y', position.get('y', 0))}, "
            f"Z={cls._format_axis_mm('z', position.get('z', 0))}"
        )

    def toggle_pause_current_task(self):
        """暂停或继续当前检测任务。"""
        if self._active_task_mode == "live" and self._is_live_running():
            target = self.live_worker
        elif self._active_task_mode == "debug" and self._is_debug_running():
            target = self.debug_worker
        else:
            return

        if self._task_paused:
            target.resume()
            self._task_paused = False
            self._start_red_light_flash()
            self.pause_btn.setText("暂停检测")
            self.statusBar().showMessage("检测已继续。", 3000)
        else:
            target.pause()
            self._task_paused = True
            self._stop_red_light_flash()
            self.pause_btn.setText("继续检测")
            self.statusBar().showMessage("检测已暂停。", 3000)

    def stop_current_task(self):
        """终止当前检测任务，并在可执行时返回本次检测的起始槽位。"""
        if self._active_task_mode is None and not self._is_live_running():
            return

        self._task_stop_requested = True
        self._task_paused = False
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("暂停检测")
        self.stop_btn.setEnabled(False)
        self.statusBar().showMessage("正在结束检测任务...", 3000)
        self._stop_red_light_flash()

        if self._is_live_running():
            self.live_worker.stop()
        elif self._active_task_mode == "debug" and self._is_debug_running():
            self.debug_worker.stop()
        elif self._active_task_mode == "live_starting":
            self._request_return_to_first_slot(self._finish_stopped_task_after_return)

    def _request_return_to_first_slot(self, callback=None):
        """请求设备回到本次检测的起始槽位；若运动中则排队到当前运动结束后执行。"""
        if callback is not None:
            self._return_to_first_slot_callback = callback
        self._pending_return_to_first_slot = True
        if self.motion_worker is not None and self.motion_worker.isRunning():
            slot_no = self._display_slot_number(self._first_detection_slot_index())
            self.statusBar().showMessage(f"当前运动完成后将返回槽位 {slot_no}。", 3000)
            return
        self._start_return_to_first_slot()

    def _start_return_to_first_slot(self):
        self._pending_return_to_first_slot = False
        callback = self._return_to_first_slot_callback

        def finish(success, message):
            self._return_to_first_slot_callback = None
            if callback is not None:
                callback(success, message)

        if not self.device_controller.is_initialized:
            finish(False, "设备尚未复位，无法自动返回检测起始槽位。")
            return

        tray_id = self.tray_combo.currentData()
        slot_index = self._first_detection_slot_index()
        try:
            first_coord = self.services.tray_manager.calculate_slot_coordinate(tray_id, slot_index)
        except ValueError as exc:
            finish(False, str(exc))
            return

        slot_no = self._display_slot_number(slot_index)
        self._run_motion_task(
            f"正在返回槽位 {slot_no}...",
            lambda: self._move_to_coordinate(first_coord["x"], first_coord["y"], first_coord["z"]),
            on_success=lambda result: finish(True, result.message),
            on_failure=lambda result: finish(False, result.message),
            show_success=False,
            modal=False,
        )

    def _finish_stopped_task_after_return(self, success, message):
        self._active_task_mode = None
        self._task_stop_requested = False
        self._set_task_controls_idle()
        slot_no = self._display_slot_number(self._first_detection_slot_index())
        if success:
            QMessageBox.information(self, "已结束", f"检测任务已结束，并已返回槽位 {slot_no}。")
        else:
            QMessageBox.warning(self, "已结束", f"检测任务已结束，但返回槽位 {slot_no} 失败：{message}")
        self.setFocus()

    def move_to_slot(self, slot_index):
        """点击槽位后，按料盘几何参数计算坐标并确认移动。"""
        if not self._validate_before_operation(require_motion_params=True):
            return
        tray_id = self.tray_combo.currentData()
        try:
            coord = self.services.tray_manager.calculate_slot_coordinate(tray_id, slot_index)
        except ValueError as exc:
            QMessageBox.warning(self, "坐标计算失败", str(exc))
            return

        slot_no = self._display_slot_number(slot_index)
        reply = QMessageBox.question(
            self,
            "移动到槽位",
            f"确定移动到槽位 {slot_no} 吗？\n\n"
            f"目标坐标：{self._format_position_mm(coord)}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_motion_task(
            f"正在移动到槽位 {slot_no}...",
            lambda: self._move_to_coordinate(coord["x"], coord["y"], coord["z"]),
        )

    def _read_current_position_for_dialog(self):
        """供新增料盘弹窗读取当前三轴坐标。"""
        if not self._validate_before_operation(require_motion_ready=True):
            return None
        result = self.device_controller.request_current_position()
        if not result.success:
            QMessageBox.warning(self, "读取坐标失败", result.message)
            return None
        return result.data

    def _jog_axis_for_tray_dialog(self, dialog, axis, pulses, speed):
        """新增/编辑料盘弹窗里的三轴点动，UI 显示 mm，底层仍按脉冲执行。"""
        if not self._validate_before_operation(require_motion_ready=True):
            return

        def after_move(result):
            position = result.data.get("position") or {}
            if position:
                dialog.set_current_position(position)

        self._run_motion_task(
            f"{axis.upper()}轴点动 {self._format_axis_mm(axis, pulses)}...",
            lambda: self.device_controller.move_axis_pulses(axis, int(pulses), int(speed)),
            on_success=after_move,
            show_success=False,
        )

    def _save_tray_origin(self, tray_id, position):
        """编辑料盘时，获取当前坐标即确认并保存该料盘原点。"""
        if not tray_id or not position:
            return
        self.services.tray_manager.update_tray(
            tray_id,
            firstSlotOrigin={
                "x": int(position.get("x", 0)),
                "y": int(position.get("y", 0)),
                "z": int(position.get("z", 0)),
            },
        )
        self.statusBar().showMessage(f"料盘 {tray_id} 原点已保存", 3000)

    def start_camera_preview(self):
        """启动摄像头预览线程。

        从应用配置读取 camera_id。若打开失败，`CameraWorker` 会持续重试，
        UI 保持"无摄像头信号"占位并显示重连状态。
        """
        app_config = self.services.config_manager.get_config()
        try:
            camera_id = int(app_config.get("camera_id", 0))
        except (TypeError, ValueError):
            camera_id = 0

        self.camera_worker = CameraWorker(camera_id)
        self.camera_worker.frame_ready.connect(self.update_camera_frame)
        self.camera_worker.status_changed.connect(self.update_camera_status)
        self.camera_worker.start()

    def update_camera_frame(self, pixmap, frame_bgr=None):
        """摄像头每帧回调，按预览框当前尺寸等比缩放后贴到 label。"""
        if frame_bgr is not None:
            self._maybe_start_chip_preview(frame_bgr)
            pixmap = self._build_camera_preview_pixmap(frame_bgr)

        target_width = max(self.camera_frame.width(), 220)
        target_height = max(self.camera_frame.height(), 140)
        scaled_pixmap = pixmap.scaled(
            target_width, target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.camera_frame.setPixmap(scaled_pixmap)

    def update_camera_status(self, message):
        """摄像头打开失败或重连时刷新预览占位文本。"""
        if message:
            self.camera_frame.clear()
            self.camera_frame.setText(message)

    def _maybe_start_chip_preview(self, frame_bgr):
        if frame_bgr is None:
            return
        if self.chip_preview_worker is not None and self.chip_preview_worker.isRunning():
            return

        now = time.monotonic()
        if now - self.chip_preview_last_started < self.chip_preview_interval_seconds:
            return

        self.chip_preview_last_started = now
        worker = ChipPreviewWorker(self.services.engine, frame_bgr.copy(), self)
        worker.result_ready.connect(self._on_chip_preview_result)
        worker.finished.connect(self._clear_chip_preview_worker)
        self.chip_preview_worker = worker
        worker.start()

    def _on_chip_preview_result(self, result):
        self.chip_preview_result = result

    def _clear_chip_preview_worker(self):
        self.chip_preview_worker = None

    def _build_camera_preview_pixmap(self, frame_bgr):
        display_frame = frame_bgr.copy()
        result = self.chip_preview_result or {}
        frame_h, frame_w = display_frame.shape[:2]
        if result.get("image_shape") == [int(frame_h), int(frame_w)]:
            self._draw_chip_preview_boxes(display_frame, result.get("chips", []))
        center_status = self.get_origin_center_status()
        self._draw_preview_center_cross(display_frame, center_status.get("ok", False))
        return self._bgr_to_pixmap(display_frame)

    @staticmethod
    def _draw_preview_center_cross(display_frame, is_centered=False):
        """在预览画面中心画固定十字，辅助芯片摆放居中。"""
        frame_h, frame_w = display_frame.shape[:2]
        center_x = frame_w // 2
        center_y = frame_h // 2
        size = max(18, min(frame_w, frame_h) // 10)
        gap = max(4, size // 6)
        color = (0, 220, 0) if is_centered else (255, 255, 255)
        shadow = (0, 0, 0)

        for draw_color, thickness in ((shadow, 3), (color, 1)):
            cv2.line(
                display_frame,
                (center_x - size, center_y),
                (center_x - gap, center_y),
                draw_color,
                thickness,
                cv2.LINE_AA,
            )
            cv2.line(
                display_frame,
                (center_x + gap, center_y),
                (center_x + size, center_y),
                draw_color,
                thickness,
                cv2.LINE_AA,
            )
            cv2.line(
                display_frame,
                (center_x, center_y - size),
                (center_x, center_y - gap),
                draw_color,
                thickness,
                cv2.LINE_AA,
            )
            cv2.line(
                display_frame,
                (center_x, center_y + gap),
                (center_x, center_y + size),
                draw_color,
                thickness,
                cv2.LINE_AA,
            )
        cv2.circle(
            display_frame,
            (center_x, center_y),
            3 if is_centered else 2,
            color,
            thickness=1,
            lineType=cv2.LINE_AA,
        )

    def get_origin_center_status(self):
        """返回首颗芯片 ROI 是否已位于画面中心。"""
        result = self.chip_preview_result or {}
        status = str(result.get("status", ""))
        image_shape = result.get("image_shape") or []
        if len(image_shape) != 2:
            return {
                "ok": False,
                "message": "等待芯片 ROI 检测...",
                "tolerance_mm": self.origin_center_tolerance_mm,
                "tolerance_px": self.origin_center_tolerance_px,
            }

        chips = result.get("chips") or []
        selected_index = int(result.get("selected_index", -1))
        if status != "success" or selected_index < 0 or selected_index >= len(chips):
            return {
                "ok": False,
                "message": "未检测到芯片 ROI，请调整芯片到画面中心。",
                "tolerance_mm": self.origin_center_tolerance_mm,
                "tolerance_px": self.origin_center_tolerance_px,
            }

        chip = chips[selected_index]
        center = chip.get("center") or []
        if len(center) != 2:
            return {
                "ok": False,
                "message": "芯片 ROI 中心无效，请重新调整。",
                "tolerance_mm": self.origin_center_tolerance_mm,
                "tolerance_px": self.origin_center_tolerance_px,
            }

        frame_h, frame_w = int(image_shape[0]), int(image_shape[1])
        dx_px = float(center[0]) - frame_w / 2.0
        dy_px = float(center[1]) - frame_h / 2.0
        dx_pulses = dx_px * self.origin_center_x_pulses_per_px
        dy_pulses = dy_px * self.origin_center_y_pulses_per_px
        dx_mm = pulses_to_mm("x", dx_pulses)
        dy_mm = pulses_to_mm("y", dy_pulses)
        ok = (
            abs(dx_px) <= self.origin_center_tolerance_px
            and abs(dy_px) <= self.origin_center_tolerance_px
        )
        distance_hint = f"X {dx_mm:+.3f} mm，Y {dy_mm:+.3f} mm"
        message = (
            f"已居中：{distance_hint}"
            if ok
            else f"请继续移动：约 {distance_hint}"
        )
        return {
            "ok": ok,
            "message": message,
            "dx_px": dx_px,
            "dy_px": dy_px,
            "dx_pulses": dx_pulses,
            "dy_pulses": dy_pulses,
            "dx_mm": dx_mm,
            "dy_mm": dy_mm,
            "tolerance_mm": self.origin_center_tolerance_mm,
            "tolerance_px": self.origin_center_tolerance_px,
            "x_pulses_per_px": self.origin_center_x_pulses_per_px,
            "y_pulses_per_px": self.origin_center_y_pulses_per_px,
        }

    @staticmethod
    def _draw_chip_preview_boxes(display_frame, chips):
        if not chips:
            return

        frame_h, frame_w = display_frame.shape[:2]
        for chip in chips:
            bbox = chip.get("bbox")
            if not bbox or len(bbox) != 4:
                continue

            x1, y1, x2, y2 = [int(v) for v in bbox]
            x1 = max(0, min(frame_w - 1, x1))
            x2 = max(0, min(frame_w - 1, x2))
            y1 = max(0, min(frame_h - 1, y1))
            y2 = max(0, min(frame_h - 1, y2))
            if x2 <= x1 or y2 <= y1:
                continue

            selected = bool(chip.get("selected", False))
            color = (0, 255, 0) if selected else (0, 210, 255)
            thickness = 3 if selected else 2
            cv2.rectangle(display_frame, (x1, y1), (x2, y2), color, thickness)

            center = chip.get("center")
            if center and len(center) == 2:
                center_x = max(0, min(frame_w - 1, int(round(center[0]))))
                center_y = max(0, min(frame_h - 1, int(round(center[1]))))
                cv2.circle(display_frame, (center_x, center_y), 3, color, -1)

    @staticmethod
    def _bgr_to_pixmap(frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        image = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(image)

    def upload_reference_image(self):
        """上传参考图片入口。

        弹出一个两选一菜单：本地文件 / 摄像头拍摄。选择后分别进入
        `_upload_from_local` 或 `_upload_from_camera`，最后都汇聚到
        `_process_reference_image` 做 OCR + 确认 + 保存模板。
        """
        if not self._validate_before_operation():
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("选择参考图片来源")
        msg.setText("请选择参考图片的获取方式：")
        local_btn = msg.addButton("本地文件", QMessageBox.ButtonRole.ActionRole)
        camera_btn = msg.addButton("摄像头拍摄", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == local_btn:
            self._upload_from_local()
        elif clicked == camera_btn:
            self._upload_from_camera()

    def _upload_from_local(self):
        """本地文件选择器拿参考图片路径。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择参考图片", "",
            "Image Files (*.png *.jpg *.jpeg *.bmp);;All Files (*)",
        )
        if file_path:
            self._process_reference_image(file_path)

    def _upload_from_camera(self):
        """从摄像头预览中拍照取参考图片。

        借用正在运行的 `camera_worker`，对话框内部会自行连接 frame_ready
        信号得到预览；用户确认后把临时文件路径回传，处理完后立刻删除。
        """
        if not self.camera_worker:
            QMessageBox.warning(self, "错误", "摄像头未启动，无法拍摄。")
            return

        dialog = CameraCaptureDialog(self.camera_worker, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            captured = dialog.get_captured_path()
            if captured and os.path.isfile(captured):
                self._process_reference_image(captured)
                try:
                    os.remove(captured)
                except OSError:
                    # 临时文件已被占用/删过都无所谓，静默跳过
                    pass

    def _ask_reference_angle(self):
        angle, ok = QInputDialog.getItem(
            self,
            "参考图片角度",
            "请选择当前参考图片的标准角度：",
            ["0", "270"],
            0,
            False,
        )
        if not ok:
            return None
        return int(angle)

    def _recognize_reference_image(self, file_path, target_angle):
        return self.services.template_manager.recognize_template_image(
            file_path,
            target_angle=target_angle,
        )

    def _process_reference_image(self, file_path):
        """处理参考图片。

        流程：
        1. 先由用户输入参考图片角度（当前演示只支持 0/270）。
        2. 按该角度把芯片转成 0 度后 OCR。
        3. 成功则弹 `TemplateConfirmDialog` 让用户校对型号字符。
        4. 用户确认后保存新模板。
        5. 同步更新 UI 和当前料盘里的 model/angle 字段。
        """
        manual_angle = self._ask_reference_angle()
        if manual_angle is None:
            return

        recognized = self._recognize_reference_image(file_path, manual_angle)
        success = recognized.get("success")
        detected_model = recognized.get("detected_model", "")
        detected_angle = manual_angle
        detected_texts = recognized.get("detected_texts", [])

        if success:
            dialog = TemplateConfirmDialog(
                detected_model,
                detected_angle,
                detected_texts=detected_texts,
                existing_models=self.services.template_manager.list_all_templates(),
                parent=self,
            )
            if hasattr(dialog, "angle_spinbox"):
                dialog.angle_spinbox.setEnabled(False)
                dialog.angle_spinbox.setToolTip("参考图片角度已在 OCR 前确认，保存时固定使用该角度。")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                final_model = dialog.get_model_name()
                final_angle = manual_angle
                selected_texts = dialog.get_selected_texts()
                tray_id = self.tray_combo.currentData()

                if not self.services.template_manager.save_template(
                    final_model,
                    final_angle,
                    image_path=file_path,
                    ocr_texts=selected_texts,
                    tray_id=tray_id,
                    description="用户从图片手动确认的模板",
                ):
                    error_msg = getattr(self.services.template_manager, "last_error", "")
                    QMessageBox.warning(self, "保存失败", error_msg or "模板保存失败。")
                    return

                # 同步 UI（两套：只读显示 + 可编辑输入）
                self.model_input.setText(final_model)
                self.model_display.setText(final_model)
                self.angle_input.setText(str(final_angle))
                self.angle_display.setText(f"{final_angle}°")

                # 把新模板挂到当前料盘上，下次切换料盘时自动恢复
                if tray_id:
                    self.services.tray_manager.update_tray(
                        tray_id,
                        model=final_model,
                        angle=final_angle,
                    )

                QMessageBox.information(
                    self, "成功",
                    f"模板已保存: {final_model} (角度: {final_angle}°，字符行数: {len(selected_texts)})\n\n"
                    "现在可以使用此模板进行检测。",
                )
        else:
            error_msg = getattr(self.services.template_manager, "last_error", "")
            detail = f"\n\n{error_msg}" if error_msg else ""
            QMessageBox.warning(
                self, "失败",
                f"无法识别参考图片，请确保图片清晰且包含芯片型号信息{detail}",
            )

    def refresh_templates(self):
        """刷新：把所有槽位重置为"待机"状态。

        之所以加实时识别忙碌检查，是因为识别中途复位会和后台线程竞争
        UI 更新（见 `update_slot_ui`）。
        """
        if (self.live_worker is not None and self.live_worker.isRunning()) or \
                (self.debug_worker is not None and self.debug_worker.isRunning()) or \
                (self.motion_worker is not None and self.motion_worker.isRunning()):
            QMessageBox.warning(self, "提示", "任务进行中，请结束后再刷新。")
            return
        for slot in self.slots:
            slot.reset()
        QMessageBox.information(self, "刷新", "界面已刷新，所有槽位已重置为待机状态。")

    def get_results_directory(self):
        """推算历史结果目录。

        优先级：
        1. `DataLogger.base_dir` 的绝对路径（如果存在）
        2. 项目根下的 `results/`（开发/演示场景）
        3. 都不存在则创建 `logger.base_dir` 并返回
        """
        logger_dir = os.path.abspath(
            getattr(self.services.data_logger, "base_dir", "results"),
        )
        module_dir = os.path.dirname(os.path.dirname(__file__))
        demo_results_dir = os.path.abspath(os.path.join(module_dir, "results"))

        if os.path.exists(logger_dir):
            return logger_dir
        if os.path.exists(demo_results_dir):
            return demo_results_dir

        os.makedirs(logger_dir, exist_ok=True)
        return logger_dir

    def start_debug_inspection(self):
        """选择图片目录并启动调试批量检测。"""
        if self._active_task_mode is not None or self._is_live_running():
            QMessageBox.warning(self, "提示", "正式检测进行中，请结束后再调试。")
            return
        if self.motion_worker is not None and self.motion_worker.isRunning():
            QMessageBox.warning(self, "提示", "运动任务进行中，请等待完成后再调试。")
            return
        if self._is_debug_running():
            QMessageBox.information(self, "提示", "调试检测正在运行，请稍候。")
            return

        initial_dir = self.services.config_manager.get_image_directory() or self.get_results_directory()
        image_dir = QFileDialog.getExistingDirectory(
            self,
            "选择调试检测目录",
            initial_dir,
        )
        if not image_dir:
            return

        if not self.services.config_manager.set_image_directory(image_dir):
            QMessageBox.warning(self, "目录无效", "无法保存调试检测目录，请确认目录仍然存在。")
            return

        image_files = self._list_detection_images(image_dir)
        if not image_files:
            QMessageBox.warning(
                self,
                "目录无图片",
                "所选目录中没有可检测图片。\n\n支持格式：.png / .jpg / .jpeg / .bmp",
            )
            return

        total_slots = len(self.slots) if self.slots else len(image_files)
        tray_id = self.tray_combo.currentData() or "DEBUG"
        tray_name = self.tray_combo.currentText().strip() or "调试检测"
        self.services.data_logger.start_new_batch(
            tray_id,
            expected_slots=total_slots,
            tray_name=tray_name,
        )

        for slot in self.slots:
            slot.reset()

        self.debug_worker = ControlWorker(
            engine=self.services.engine,
            img_dir=image_dir,
            target_m=self._current_template_match_texts(),
            target_a=self.angle_input.text(),
            data_logger=self.services.data_logger,
            total_slots=total_slots,
        )
        self.debug_worker.progress_update.connect(self.update_slot_ui)
        self.debug_worker.finished.connect(self.on_debug_inspection_finished)
        self._active_task_mode = "debug"
        self._task_stop_requested = False
        self._task_paused = False
        self._set_task_controls_running(pause_enabled=True)
        self.statusBar().showMessage(f"调试检测运行中：{image_dir}", 5000)
        self.debug_worker.start()

    def on_debug_inspection_finished(self):
        """调试批量检测结束后恢复按钮状态并保存界面截图。"""
        stopped = self._task_stop_requested or bool(getattr(self.debug_worker, "was_stopped", False))
        if not stopped:
            self.services.data_logger.save_ui_screenshot(self)
        self.debug_worker = None
        self._active_task_mode = None
        self._task_stop_requested = False
        self._set_task_controls_idle()
        if stopped:
            QMessageBox.information(self, "已结束", "调试检测已结束。")
        else:
            QMessageBox.information(self, "完成", "调试检测已完成。")
        self.setFocus()

    @staticmethod
    def _list_detection_images(directory):
        try:
            return [
                name for name in os.listdir(directory)
                if name.lower().endswith(DETECTION_IMAGE_EXTENSIONS)
            ]
        except OSError:
            return []

    def open_history_data(self):
        """选择并预览历史表格/图片文件。"""
        results_dir = self.get_results_directory()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择历史数据文件",
            results_dir,
            "历史数据 (*.csv *.jpg *.jpeg *.png *.bmp);;表格文件 (*.csv);;图片文件 (*.jpg *.jpeg *.png *.bmp);;所有文件 (*)",
        )

        if not file_path:
            return

        suffix = os.path.splitext(file_path)[1].lower()
        if suffix == ".csv":
            self.show_csv_preview(file_path)
        elif suffix in {".jpg", ".jpeg", ".png", ".bmp"}:
            self.show_image_preview(file_path)
        else:
            QMessageBox.information(self, "提示", f"暂不支持预览该类型文件：{suffix}")

    def show_csv_preview(self, file_path):
        """弹窗显示 CSV 内容（只读文本框）。

        自动处理常见编码：先按 UTF-8-SIG 读，失败则回退到 GBK（Windows 下
        Excel 默认保存的中文 CSV 常用 GBK）。
        """
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="gbk", errors="replace") as f:
                content = f.read()
        except Exception as e:
            QMessageBox.warning(self, "打开失败", f"无法读取文件：{e}")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"历史数据表格 - {os.path.basename(file_path)}")
        dialog.resize(960, 640)

        layout = QVBoxLayout(dialog)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(content)
        viewer.setStyleSheet(S.CSV_VIEWER)
        layout.addWidget(viewer)

        dialog.exec()

    def show_image_preview(self, file_path):
        """弹窗显示图片，右上角叠加半透明悬浮操作按钮。

        支持全屏 / 缩小 / 退出三个浮动按钮；图片超出窗口时会等比缩放。
        """
        original_pixmap = QPixmap(file_path)
        if original_pixmap.isNull():
            QMessageBox.warning(self, "打开失败", "无法加载图片，请检查文件是否损坏。")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"历史图片 - {os.path.basename(file_path)}")
        dialog.resize(960, 640)
        dialog.setStyleSheet("QDialog { background-color: #0a0e1a; }")

        grid = QGridLayout(dialog)
        grid.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        def _fit_pixmap(max_w, max_h):
            """把原图等比缩到不超过 (max_w, max_h)，小图不会放大。"""
            if original_pixmap.width() > max_w or original_pixmap.height() > max_h:
                return original_pixmap.scaled(
                    max_w, max_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            return original_pixmap

        image_label.setPixmap(_fit_pixmap(1600, 1000))
        scroll.setWidget(image_label)
        grid.addWidget(scroll, 0, 0)

        # 右上角浮动操作按钮（透明容器，不拦截图片区域的鼠标事件）
        overlay = QWidget()
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        overlay.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(overlay)
        btn_layout.setContentsMargins(0, 10, 10, 0)
        btn_layout.setSpacing(6)

        fullscreen_btn = QPushButton("全屏")
        fullscreen_btn.setStyleSheet(S.OVERLAY_BUTTON)

        def _go_fullscreen():
            dialog.showFullScreen()
            screen = QApplication.primaryScreen().size()
            image_label.setPixmap(_fit_pixmap(screen.width(), screen.height()))

        fullscreen_btn.clicked.connect(_go_fullscreen)
        btn_layout.addWidget(fullscreen_btn)

        shrink_btn = QPushButton("缩小")
        shrink_btn.setStyleSheet(S.OVERLAY_BUTTON)

        def _go_normal():
            dialog.showNormal()
            dialog.resize(960, 640)
            image_label.setPixmap(_fit_pixmap(1600, 1000))

        shrink_btn.clicked.connect(_go_normal)
        btn_layout.addWidget(shrink_btn)

        close_btn = QPushButton("退出")
        close_btn.setStyleSheet(S.OVERLAY_BUTTON)
        close_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(close_btn)

        grid.addWidget(overlay, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        dialog.exec()

    def update_slot_ui(self, index, status, color_key):
        """实时识别槽位结果回调。

        Parameters
        ----------
        index : int
            0 基准的槽位索引。
        status : str
            中文状态文本（"正常" / "异常" / "识别失败" 等）。
        color_key : str
            颜色键（"green" / "red" / "default"），由 `MaterialSlot.set_result` 映射成背景色。
        """
        if index < len(self.slots):
            self.slots[index].set_result(status, color_key)

    # ==================================================================
    # 实时识别模式
    # ==================================================================

    def start_live_inspection(self):
        """启动实时识别任务。

        前置校验：
        - 摄像头必须已启动（live 模式靠摄像头帧推理）；
        - 实时识别同时只能运行一个；

        执行流程：
        1. 先移动到当前料盘本次检测顺序的起始槽位；
        2. 建立新的 CSV 批次记录并重置所有槽位；
        3. 启动 ``LiveInspectionWorker``，后续槽位由 UI 自动移槽后继续。
        """
        if not self._validate_before_operation(require_motion_params=True):
            return

        if not self.camera_worker or not self.camera_worker.isRunning():
            QMessageBox.warning(self, "错误", "摄像头未启动，无法进入实时识别模式。")
            return
        if getattr(self.camera_worker, "current_frame_bgr", None) is None:
            QMessageBox.warning(self, "错误", "摄像头暂无有效画面，请等待重连成功后再启动实时识别。")
            return

        if self.live_worker is not None and self.live_worker.isRunning():
            QMessageBox.warning(self, "提示", "已有任务运行中，请等待完成后再启动实时识别。")
            return
        if self.motion_worker is not None and self.motion_worker.isRunning():
            QMessageBox.warning(self, "提示", "运动任务进行中，请等待完成后再启动实时识别。")
            return

        tray_id = self.tray_combo.currentData()
        rows, cols = self.services.tray_manager.get_tray_dimensions(tray_id)
        slot_order = self._build_natural_slot_order(rows, cols)
        if not slot_order:
            QMessageBox.warning(self, "提示", "当前料盘没有可检测槽位。")
            return
        first_slot_index = slot_order[0]
        try:
            first_coord = self.services.tray_manager.calculate_slot_coordinate(tray_id, first_slot_index)
        except ValueError as exc:
            QMessageBox.warning(self, "坐标计算失败", str(exc))
            return

        self._current_slot_order = slot_order
        self._active_task_mode = "live_starting"
        self._task_stop_requested = False
        self._task_paused = False
        self._set_task_controls_running(pause_enabled=False)
        first_slot_no = self._display_slot_number(first_slot_index)
        self._run_motion_task(
            f"正在移动到槽位 {first_slot_no}...",
            lambda: self._move_to_coordinate(first_coord["x"], first_coord["y"], first_coord["z"]),
            on_success=lambda _result: self._on_live_start_motion_done(tray_id),
            on_failure=self._on_live_start_motion_failed,
            show_success=False,
            modal=False,
        )

    def _on_live_start_motion_done(self, tray_id):
        if self._task_stop_requested:
            self._request_return_to_first_slot(self._finish_stopped_task_after_return)
            return
        self._start_live_worker(tray_id)

    def _current_template_match_texts(self):
        """返回当前模板用于检测判定的标准字符行，优先使用用户保存的多选行。"""
        model_name = self.model_input.text().strip()
        targets = []
        if model_name:
            template = self.services.template_manager.get_template(model_name)
            if template:
                targets = [
                    str(text).strip()
                    for text in template.get("ocrTexts", [])
                    if str(text).strip()
                ]
                if not targets:
                    fallback = (
                        template.get("standardChipModel")
                        or template.get("modelName")
                        or model_name
                    )
                    targets = [str(fallback).strip()] if str(fallback).strip() else []

        return targets or ([model_name] if model_name else [])

    def _start_live_worker(self, tray_id):
        """首槽移动到位后启动实时识别线程。"""
        self._active_task_mode = "live"
        self._set_task_controls_running(pause_enabled=True)
        self.services.data_logger.start_new_batch(
            tray_id,
            expected_slots=len(self.slots),
            tray_name=self.tray_combo.currentText(),
        )

        for slot in self.slots:
            slot.reset()

        app_config = self.services.config_manager.get_config()
        self.live_worker = LiveInspectionWorker(
            engine=self.services.engine,
            camera_worker=self.camera_worker,
            target_m=self._current_template_match_texts(),
            target_a=self.angle_input.text(),
            data_logger=self.services.data_logger,
            total_slots=len(self.slots),
            slot_order=self._current_slot_order,
            min_retry_rounds=app_config.get("live_min_retry_rounds", 3),
            max_retry_rounds=app_config.get("live_max_retry_rounds", 6),
            capture_settle_ms=app_config.get("live_capture_settle_ms", 800),
            mode="auto",
        )
        self.live_worker.slot_recognized.connect(self.update_slot_ui)
        self.live_worker.request_move_confirm.connect(self.on_live_request_move_confirm)
        self.live_worker.all_done.connect(self.on_live_all_done)
        self.live_worker.start()
        self._start_red_light_flash()

    def _on_live_start_motion_failed(self, result):
        self._stop_red_light_flash()
        self._active_task_mode = None
        self._task_stop_requested = False
        self._set_task_controls_idle()
        QMessageBox.warning(self, "实时识别启动失败", result.message)

    def on_live_request_move_confirm(self, next_slot_index):
        """实时识别线程请求下一槽位时，自动移动并继续识别。

        Parameters
        ----------
        next_slot_index : int
            下一个待识别的槽位索引（0 基准）；对话框显示时转成 1 基准。
        """
        tray_id = self.tray_combo.currentData()
        try:
            coord = self.services.tray_manager.calculate_slot_coordinate(tray_id, next_slot_index)
        except ValueError as exc:
            QMessageBox.warning(self, "自动移槽失败", str(exc))
            if self.live_worker is not None:
                self.live_worker.stop()
            return

        def after_move(_result):
            if self._task_stop_requested:
                return
            if self.live_worker is not None:
                self.live_worker.confirm_move()

        self._run_motion_task(
            f"自动移动到槽位 {self._display_slot_number(next_slot_index)}...",
            lambda: self._move_to_coordinate(coord["x"], coord["y"], coord["z"]),
            on_success=after_move,
            on_failure=self._on_live_move_failed,
            show_success=False,
            modal=False,
        )

    def _on_live_move_failed(self, result):
        self._stop_red_light_flash()
        if self.live_worker is not None:
            self.live_worker.stop()
        QMessageBox.warning(self, "自动移槽失败", result.message)

    def on_live_all_done(self):
        """实时识别线程全部完成后的回调（在主线程执行）。

        保存截图、返回料盘原点、恢复按钮并弹提示。
        """
        self._stop_red_light_flash()
        stopped = self._task_stop_requested or bool(getattr(self.live_worker, "was_stopped", False))
        self.live_worker = None

        if stopped:
            self._request_return_to_first_slot(self._finish_stopped_task_after_return)
            return

        self.services.data_logger.save_ui_screenshot(self)
        self._request_return_to_first_slot(self._finish_live_completed_after_return)

    def _finish_live_completed_after_return(self, success, message):
        self._active_task_mode = None
        self._task_stop_requested = False
        self._set_task_controls_idle()
        slot_no = self._display_slot_number(self._first_detection_slot_index())
        if success:
            QMessageBox.information(self, "完成", f"实时识别已完成，并已返回槽位 {slot_no}。")
        else:
            QMessageBox.warning(self, "回槽位失败", f"实时识别已完成，但返回槽位 {slot_no} 失败：{message}")
        self.setFocus()

    def keyPressEvent(self, event):
        """全局按键：ESC 关闭窗口，其余交给父类处理。"""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """窗口关闭前确保所有后台线程干净退出，避免进程僵死。"""
        self._stop_red_light_flash()
        # 停止实时识别（stop() 会 set() 内部 Event，让线程从 wait() 中退出）
        if self.live_worker is not None and self.live_worker.isRunning():
            self.live_worker.stop()
            self.live_worker.wait()
        if self.motion_worker is not None and self.motion_worker.isRunning():
            self.motion_worker.wait()
        if self.chip_preview_worker is not None and self.chip_preview_worker.isRunning():
            self.chip_preview_worker.wait()
        if self.debug_worker is not None and self.debug_worker.isRunning():
            self.debug_worker.stop()
            self.debug_worker.wait()
        if self.camera_worker:
            self.camera_worker.stop()
            self.camera_worker.wait()
        if self.device_controller:
            self.device_controller.close()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OCRApp()
    window.show()
    sys.exit(app.exec())
