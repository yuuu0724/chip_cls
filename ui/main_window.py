"""主应用窗口 - AI 芯片料盘视觉检测系统。

本模块只负责 UI 编排与事件粘合，所有业务服务（OCR 引擎、模板/料盘/配置
管理、日志）通过 `AppServices` 容器注入，方便替换与测试。

主要职责
--------
1. 构建左右两栏界面（左：料盘网格 + 顶部控制，右：摄像头/配置/任务控制）。
2. 响应用户操作：料盘切换、新增/删除料盘、上传参考图片、开始检测、刷新。
3. 管理后台线程：摄像头预览 `CameraWorker` + 批量检测 `ControlWorker`。
"""
import os
import sys

import cv2
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
    LightAdjustDialog,
    TemplateConfirmDialog,
)
from .material_slot import MaterialSlot


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
    self.worker : ControlWorker | None
        批量检测线程；单批运行期间非 None，用来防重入。
    self.slots : list[MaterialSlot]
        当前料盘展开的全部槽位组件，顺序与料位编号一致。
    self.img_dir : str | None
        用户选择的图像目录，检测前必须非空。
    """

    def __init__(self, services: AppServices | None = None):
        super().__init__()
        self.setWindowTitle("AI 芯片料盘视觉检测系统")
        self.setStyleSheet(S.MAIN_WINDOW)

        # 服务容器：UI 只依赖这一个对象，解耦具体实现
        self.services = services if services is not None else AppServices.create_default()
        if getattr(self.services, "device_controller", None) is None:
            self.services.device_controller = DeviceController(
                self.services.config_manager.get_config()
            )
        self.device_controller = self.services.device_controller

        # UI 相关状态
        self.camera_worker = None   # 摄像头预览线程（懒启动）
        self.worker = None          # 正在运行的批量检测线程（None 表示空闲）
        self.live_worker = None     # 正在运行的实时识别线程（None 表示空闲）
        self.motion_worker = None   # 正在运行的运动控制线程
        self.slots = []             # 当前料盘的槽位组件列表
        self.img_dir = None         # 用户选择的图像目录
        self.current_light_config = {"light1Voltage": 0.0, "light2Voltage": 0.0}
        self.grayscale_enabled = False

        self.init_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.centralWidget().setEnabled(False)

        # 启动后立刻读取历史配置里的图像目录（若已保存过）
        self.check_image_directory()
        QTimer.singleShot(0, self.ensure_startup_motion_ready)

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
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # ========== 左侧：料位网格 + 顶部控制 ==========
        left_layout = QVBoxLayout()
        left_layout.setSpacing(8)

        # --- 顶部料盘选择和参数区 ---
        top_control_layout = QHBoxLayout()
        top_control_layout.setSpacing(8)

        # 料盘选择下拉
        tray_label = QLabel("料盘:")
        tray_label.setStyleSheet(S.LABEL_TITLE)
        top_control_layout.addWidget(tray_label)

        self.tray_combo = QComboBox()
        self.tray_combo.setMaximumWidth(140)
        self.tray_combo.setMinimumHeight(42)
        self.tray_combo.setStyleSheet(S.TRAY_COMBO)

        # 从 tray_manager 拉取全部料盘写入下拉（userData 用 tray_id 便于反查）
        for tray_id in self.services.tray_manager.get_tray_list():
            self.tray_combo.addItem(tray_id, tray_id)

        self.tray_combo.currentIndexChanged.connect(self.on_tray_changed)
        top_control_layout.addWidget(self.tray_combo)

        # 新增料盘按钮
        add_tray_btn = QPushButton("＋ 新增料盘")
        add_tray_btn.setMinimumHeight(42)
        add_tray_btn.setStyleSheet(S.ADD_TRAY_BTN)
        add_tray_btn.clicked.connect(self.add_new_tray)
        top_control_layout.addWidget(add_tray_btn)

        edit_tray_btn = QPushButton("编辑料盘")
        edit_tray_btn.setMinimumHeight(42)
        edit_tray_btn.setStyleSheet(S.ADD_TRAY_BTN)
        edit_tray_btn.clicked.connect(self.edit_current_tray)
        top_control_layout.addWidget(edit_tray_btn)

        # 删除料盘按钮（红色警示色）
        delete_tray_btn = QPushButton("－ 删除料盘")
        delete_tray_btn.setMinimumHeight(42)
        delete_tray_btn.setStyleSheet(S.DELETE_TRAY_BTN)
        delete_tray_btn.clicked.connect(self.delete_current_tray)
        top_control_layout.addWidget(delete_tray_btn)

        # 型号实时显示（只读文字）
        model_label = QLabel("型号:")
        model_label.setStyleSheet(S.LABEL_TITLE)
        top_control_layout.addWidget(model_label)

        self.model_display = QLabel("ATMLH904")
        self.model_display.setStyleSheet(S.VALUE_HIGHLIGHT)
        self.model_display.setMinimumWidth(140)
        top_control_layout.addWidget(self.model_display)

        # 角度实时显示（只读文字）
        angle_label = QLabel("角度:")
        angle_label.setStyleSheet(S.LABEL_TITLE)
        top_control_layout.addWidget(angle_label)

        self.angle_display = QLabel("90°")
        self.angle_display.setStyleSheet(S.VALUE_HIGHLIGHT)
        self.angle_display.setMinimumWidth(60)
        top_control_layout.addWidget(self.angle_display)

        top_control_layout.addStretch()

        left_layout.addLayout(top_control_layout)
        
        # --- 料位网格区（根据料盘规格动态生成） ---
        grid_container = QWidget()
        grid_container.setStyleSheet("background-color: #1a1f2e;")
        self.grid_layout = QGridLayout(grid_container)
        self.grid_layout.setSpacing(12)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 首次用当前选中料盘的规格铺网格
        first_tray_id = self.tray_combo.currentData()
        rows, cols = (
            self.services.tray_manager.get_tray_dimensions(first_tray_id)
            if first_tray_id else (3, 7)
        )
        self._rebuild_grid(rows, cols)

        left_layout.addWidget(grid_container, 1)

        # ========== 右侧：摄像头 + 配置中心 + 任务控制 ==========
        right_layout = QVBoxLayout()
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

        self.grayscale_btn = QPushButton("灰度图像：关")
        self.grayscale_btn.setCheckable(True)
        self.grayscale_btn.setMinimumHeight(30)
        self.grayscale_btn.setStyleSheet(S.GRAYSCALE_TOGGLE_BUTTON)
        self.grayscale_btn.clicked.connect(self.toggle_grayscale_mode)
        camera_section_layout.addWidget(self.grayscale_btn)

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

        # 设置图像目录（首次进入必须配置，否则开始检测会被拒绝）
        set_img_dir_btn = QPushButton("设置图像目录")
        set_img_dir_btn.setMinimumHeight(38)
        set_img_dir_btn.setStyleSheet(S.PRIMARY_BUTTON)
        set_img_dir_btn.clicked.connect(self.set_image_directory)
        param_section_layout.addWidget(set_img_dir_btn)

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

        light_btn = QPushButton("调光")
        light_btn.setMinimumHeight(32)
        light_btn.setStyleSheet(S.REFRESH_BUTTON)
        light_btn.clicked.connect(lambda: self._run_light_adjustment(self.current_light_config))
        param_section_layout.addWidget(light_btn)

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
        self.start_btn.clicked.connect(self.run_detection_task)
        button_section_layout.addWidget(self.start_btn)

        # 实时识别：摄像头逐槽位采集模式
        live_row = QHBoxLayout()
        live_row.setSpacing(5)

        self.live_btn = QPushButton("实时识别")
        self.live_btn.setMinimumHeight(38)
        self.live_btn.setStyleSheet(S.LIVE_BUTTON)
        self.live_btn.clicked.connect(self.start_live_inspection)
        live_row.addWidget(self.live_btn)

        button_section_layout.addLayout(live_row)

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

        # 左右布局比例 5:2，适当放大右侧摄像头预览宽度
        main_layout.addLayout(left_layout, 5)
        main_layout.addLayout(right_layout, 2)

        # 工业屏直接全屏，避免标题栏占用可视区
        self.showFullScreen()

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
        tray_info = self.services.tray_manager.get_tray_info(tray_id) or {}
        self.current_light_config = dict(
            tray_info.get("lightConfig") or self.current_light_config
        )

        rows, cols = self.services.tray_manager.get_tray_dimensions(tray_id)
        self._rebuild_grid(rows, cols)
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
            lambda: self.device_controller.move_to_coordinate(
                origin_coord["x"], origin_coord["y"], origin_coord["z"], self._motion_speed()
            ),
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

        total = rows * cols
        for i in range(total):
            slot = MaterialSlot(i + 1)
            slot.setMinimumSize(70, 70)
            slot.clicked.connect(self.move_to_slot)
            self.grid_layout.addWidget(slot, i // cols, i % cols)
            self.slots.append(slot)

    def add_new_tray(self):
        """弹出"新增料盘"对话框并把结果写回 tray_manager。

        用户点击确认后：
        1. 以 `料盘 {tray_id}` 为默认名写入配置
        2. 追加到下拉末尾并切换过去（触发 `on_tray_changed` 重建网格）
        """
        if not self.device_controller.is_initialized:
            QMessageBox.warning(self, "禁止操作", "设备尚未回零，禁止新增料盘。")
            return
        if self.motion_worker is not None and self.motion_worker.isRunning():
            QMessageBox.warning(self, "提示", "运动任务进行中，请等待完成后再新增料盘。")
            return

        self._run_motion_task(
            "正在回到机械原点...",
            self.device_controller.home,
            on_success=lambda _result: self._open_add_tray_dialog(),
            show_success=False,
        )

    def _open_add_tray_dialog(self):
        """机械回零完成后打开新增料盘弹窗。"""
        dialog = AddTrayDialog(
            self.services.tray_manager.get_tray_list(),
            coordinate_provider=self._read_current_position_for_dialog,
            light_adjuster=lambda cfg: self._run_light_adjustment(
                cfg, persist_to_current_tray=False
            ),
            parent=self,
        )
        dialog.jog_requested.connect(lambda axis, pulses, speed: self._jog_axis_for_tray_dialog(dialog, axis, pulses, speed))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        tray_data = dialog.get_tray_data()
        tray_id = tray_data["tray_id"]
        spec_key = tray_data["spec"]
        self.services.tray_manager.add_tray(
            tray_id,
            name=tray_id,
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
            light_config=tray_data["light_config"],
        )

        tray_info = self.services.tray_manager.get_tray_info(tray_id)
        self.tray_combo.addItem(tray_id, tray_id)
        self.tray_combo.setCurrentIndex(self.tray_combo.count() - 1)
        QMessageBox.information(self, "新增成功", f"料盘 {tray_id} 已新增。")

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
            light_adjuster=lambda cfg: self._run_light_adjustment(
                cfg, persist_to_current_tray=False
            ),
            parent=self,
            initial_data={"tray_id": tray_id, **tray_info},
            edit_mode=True,
        )
        dialog.jog_requested.connect(lambda axis, pulses, speed: self._jog_axis_for_tray_dialog(dialog, axis, pulses, speed))
        dialog.origin_saved.connect(lambda position: self._save_tray_origin(tray_id, position))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        tray_data = dialog.get_tray_data()
        self.services.tray_manager.update_tray(
            tray_id,
            name=tray_id,
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
            lightConfig=tray_data.get("light_config", {}),
        )
        index = self.tray_combo.currentIndex()
        self.tray_combo.setItemText(index, tray_id)
        self.on_tray_changed()
        QMessageBox.information(self, "保存成功", f"料盘 {tray_id} 已更新。")

    def delete_current_tray(self):
        """删除当前下拉里选中的料盘。

        拒绝条件（依次检查）：
        - 没有选中任何料盘
        - 下拉里仅剩 1 项（至少要留一个）
        - 正在运行检测任务
        - 二次确认被用户取消
        - 底层 `tray_manager.delete_tray` 拒绝删除（如默认 A0001 受保护）
        """
        tray_id = self.tray_combo.currentData()
        if not tray_id:
            QMessageBox.warning(self, "提示", "当前没有选中的料盘。")
            return

        if self.tray_combo.count() <= 1:
            QMessageBox.warning(self, "提示", "至少保留一个料盘，不能全部删除。")
            return

        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "检测任务进行中，请结束后再删除。")
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除料盘 {tray_id} 吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if not self.services.tray_manager.delete_tray(tray_id):
            QMessageBox.warning(
                self, "删除失败",
                f"编号 {tray_id} 不允许删除（默认料盘受保护）。",
            )
            return

        # 从下拉里移除当前项；Qt 会自动选中相邻项并触发 on_tray_changed 重建网格
        index = self.tray_combo.currentIndex()
        self.tray_combo.removeItem(index)

    def ensure_startup_motion_ready(self):
        """软件启动后自动连接 Modbus RTU 并触发真实机械回零。"""
        self._ensure_tray_selected_on_entry()
        self._run_motion_task(
            "正在连接控制器并等待机械回零完成...",
            lambda: self._connect_then_home(),
            on_success=self._on_motion_ready,
            on_failure=self._on_startup_motion_failed,
            show_success=False,
        )

    def _connect_then_home(self):
        port = getattr(self.device_controller, "port", "COM14")
        result = self.device_controller.connect(port)
        if not result.success:
            return result
        return self.device_controller.home()

    def _on_motion_ready(self, result):
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

    def _ensure_tray_selected_on_entry(self):
        """进入操作界面后确保至少选中一个料盘。"""
        if self.tray_combo.count() <= 0 or not self.tray_combo.currentData():
            QMessageBox.information(self, "请选择料盘", "请先选择已有料盘，或新增一个料盘。")

    def _validate_before_operation(self, require_motion_params=False):
        """统一校验设备回零、料盘选择和运动参数。"""
        if not self.device_controller.is_initialized:
            QMessageBox.warning(self, "禁止操作", "设备尚未回零，禁止执行该操作。")
            return False

        tray_id = self.tray_combo.currentData()
        if not tray_id:
            QMessageBox.warning(self, "提示", "请先选择或新增料盘。")
            return False

        if require_motion_params:
            ok, message = self.services.tray_manager.is_tray_config_complete(tray_id)
            if not ok:
                QMessageBox.warning(self, "料盘参数不完整", message)
                return False
        return True

    def _run_motion_task(self, title, task, on_success=None, on_failure=None, show_success=True):
        """在后台线程执行运动控制任务，并把结果回到主线程处理。"""
        if self.motion_worker is not None and self.motion_worker.isRunning():
            QMessageBox.warning(self, "运动任务进行中", "请等待当前运动任务完成后再操作。")
            return

        progress = QProgressDialog(title, "", 0, 0, self)
        progress.setWindowTitle("运动控制")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
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

        worker.finished.connect(handle_finished)
        worker.start()

    def _motion_speed(self):
        return 1000

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

        slot_no = slot_index + 1
        reply = QMessageBox.question(
            self,
            "移动到槽位",
            f"确定移动到槽位 {slot_no} 吗？\n\n"
            f"目标坐标：X={int(coord['x'])}, Y={int(coord['y'])}, Z={int(coord['z'])} 脉冲",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_motion_task(
            f"正在移动到槽位 {slot_no}...",
            lambda: self.device_controller.move_to_coordinate(
                coord["x"], coord["y"], coord["z"], self._motion_speed()
            ),
        )

    def _read_current_position_for_dialog(self):
        """供新增料盘弹窗读取当前三轴坐标。"""
        if not self._validate_before_operation():
            return None
        result = self.device_controller.request_current_position()
        if not result.success:
            QMessageBox.warning(self, "读取坐标失败", result.message)
            return None
        return result.data

    def _jog_axis_for_tray_dialog(self, dialog, axis, pulses, speed):
        """新增/编辑料盘弹窗里的三轴点动。"""
        if not self._validate_before_operation():
            return

        def after_move(result):
            position = result.data.get("position") or {}
            if position:
                dialog.set_current_position(position)

        self._run_motion_task(
            f"{axis.upper()}轴点动 {int(pulses)} 脉冲...",
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

    def _run_light_adjustment(self, initial_config=None, persist_to_current_tray=True):
        """打开调光面板并返回保存后的两路光源参数。"""
        dialog = LightAdjustDialog(
            self.camera_worker,
            self.device_controller,
            self.services.engine,
            initial_config or self.current_light_config,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.current_light_config = dialog.get_light_config()
            tray_id = self.tray_combo.currentData()
            if persist_to_current_tray and tray_id:
                self.services.tray_manager.update_tray(
                    tray_id, lightConfig=self.current_light_config,
                )
            return self.current_light_config
        return None

    def start_camera_preview(self):
        """启动摄像头预览线程。

        默认打开 camera_id=1（通常是外接相机）。若打开失败，`CameraWorker`
        内部会悄悄结束 run()，UI 将保持"无摄像头信号"占位。
        """
        self.camera_worker = CameraWorker(1)
        self.camera_worker.set_grayscale_enabled(self.grayscale_enabled)
        self.camera_worker.frame_ready.connect(self.update_camera_frame)
        self.camera_worker.start()

    def toggle_grayscale_mode(self, checked):
        self.grayscale_enabled = bool(checked)
        self.grayscale_btn.setText("灰度图像：开" if self.grayscale_enabled else "灰度图像：关")
        if self.camera_worker:
            self.camera_worker.set_grayscale_enabled(self.grayscale_enabled)
        message = (
            "灰度图像已开启，预览和识别输入将使用灰度图。"
            if self.grayscale_enabled
            else "灰度图像已关闭，预览和识别输入恢复彩色图。"
        )
        self.statusBar().showMessage(message, 3000)

    def update_camera_frame(self, pixmap):
        """摄像头每帧回调，按预览框当前尺寸等比缩放后贴到 label。"""
        target_width = max(self.camera_frame.width(), 220)
        target_height = max(self.camera_frame.height(), 140)
        scaled_pixmap = pixmap.scaled(
            target_width, target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.camera_frame.setPixmap(scaled_pixmap)

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

    def _recognize_reference_image(self, file_path):
        if not self.grayscale_enabled:
            return self.services.template_manager.recognize_template_image(file_path)

        image = cv2.imread(str(file_path))
        if image is None:
            return self.services.template_manager.recognize_template_image(file_path)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        result = self.services.engine.predict_image_from_array(gray_bgr)
        self.services.template_manager.last_error = result.get("status", "")

        if str(result.get("status", "")).startswith("error"):
            return {
                "success": False,
                "detected_model": "",
                "detected_angle": 0,
                "detected_texts": [],
                "error": self.services.template_manager.last_error,
            }

        detected_texts = [
            str(text) for text in (result.get("all_texts") or result.get("texts", []))
        ]
        detected_model = ""
        if detected_texts:
            detected_model = self.services.template_manager._normalize_model_text(
                detected_texts[0]
            )

        return {
            "success": True,
            "detected_model": detected_model,
            "detected_angle": int(result.get("angle", 0) or 0),
            "detected_texts": detected_texts,
            "error": self.services.template_manager.last_error,
        }

    def _process_reference_image(self, file_path):
        """处理参考图片。

        流程：
        1. 调 `template_manager.add_template_from_image` 做 OCR，拿到
           `(detected_model, detected_angle, success)`。
        2. 成功则弹 `TemplateConfirmDialog` 让用户校对型号/角度。
        3. 用户若改过参数，就把旧模板删掉、保存新模板。
        4. 同步更新 UI 和当前料盘里的 model/angle 字段。
        """
        recognized = self._recognize_reference_image(file_path)
        success = recognized.get("success")
        detected_model = recognized.get("detected_model", "")
        detected_angle = recognized.get("detected_angle", 0)
        detected_texts = recognized.get("detected_texts", [])

        if success:
            dialog = TemplateConfirmDialog(
                detected_model,
                detected_angle,
                detected_texts=detected_texts,
                existing_models=self.services.template_manager.list_all_templates(),
                current_light_config=self.current_light_config,
                light_adjuster=lambda cfg: self._run_light_adjustment(
                    cfg, persist_to_current_tray=False
                ),
                parent=self,
            )
            if dialog.exec() == QDialog.DialogCode.Accepted:
                final_model = dialog.get_model_name()
                final_angle = dialog.get_angle()
                light_config = dialog.get_light_config()
                tray_id = self.tray_combo.currentData()

                if not self.services.template_manager.save_template(
                    final_model,
                    final_angle,
                    image_path=file_path,
                    ocr_texts=detected_texts,
                    tray_id=tray_id,
                    light_config=light_config,
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
                self.current_light_config = light_config

                # 把新模板挂到当前料盘上，下次切换料盘时自动恢复
                if tray_id:
                    self.services.tray_manager.update_tray(
                        tray_id,
                        model=final_model,
                        angle=final_angle,
                        lightConfig=light_config,
                    )

                QMessageBox.information(
                    self, "成功",
                    f"模板已保存: {final_model} (角度: {final_angle}°)\n\n"
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

        之所以加 worker 忙碌检查，是因为检测中途复位会和后台线程竞争
        UI 更新（见 `update_slot_ui`）。
        """
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "检测任务进行中，请结束后再刷新。")
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

    def run_detection_task(self):
        """启动一轮批量检测。

        前置校验：
        - 必须已设置图像目录，否则直接拒绝
        - 若上一批检测尚未结束，直接忽略本次点击（防重入）

        执行流程：
        1. 调 `DataLogger.start_new_batch` 创建新的 CSV 文件（文件名带
           tray_id + 批次序号 + 时间戳）
        2. 把 UI 所有槽位恢复到"待机"
        3. 启动 `ControlWorker` 线程异步跑 OCR，每完成一格通过
           `progress_update` 信号回到 `update_slot_ui`
        """
        if not self._validate_before_operation():
            return

        if not self.img_dir:
            QMessageBox.warning(
                self, "错误",
                "请先设置图像目录！\n点击上方'设置图像目录'按钮进行配置。",
            )
            return

        # 防重入：避免用户在检测过程中连点
        if self.worker is not None and self.worker.isRunning():
            return

        tray_id = self.tray_combo.currentData()
        self.services.data_logger.start_new_batch(
            tray_id, expected_slots=len(self.slots),
        )

        self.start_btn.setEnabled(False)
        for slot in self.slots:
            slot.reset()

        target_m = self.model_input.text()
        target_a = self.angle_input.text()

        self.worker = ControlWorker(
            self.services.engine,
            self.img_dir,
            target_m,
            target_a,
            self.services.data_logger,
            total_slots=len(self.slots),
            grayscale_enabled=self.grayscale_enabled,
        )
        self.worker.progress_update.connect(self.update_slot_ui)
        self.worker.finished.connect(self.on_task_finished)
        self.worker.start()

    def update_slot_ui(self, index, status, color_key):
        """`ControlWorker.progress_update` 槽函数。

        Parameters
        ----------
        index : int
            0 基准的槽位索引。
        status : str
            中文状态文本（"正常" / "方向错误" 等）。
        color_key : str
            颜色键（"green" / "red" / "default"），由 `MaterialSlot.set_result` 映射成背景色。
        """
        if index < len(self.slots):
            self.slots[index].set_result(status, color_key)

    def on_task_finished(self):
        """检测线程 finished 信号回调。

        在主线程里做的事：
        1. 保存批次界面截图（`save_ui_screenshot` 必须在 Qt 主线程执行，否则
           最后一格可能闪退——见 data/logger.py 注释）。
        2. 重新启用"开始检测"按钮，并把 worker 置空释放引用。
        3. 弹提示框告知用户。
        """
        self.services.data_logger.save_ui_screenshot(self)
        self.start_btn.setEnabled(True)
        self.worker = None
        QMessageBox.information(self, "完成", "批量检测已完成！")
        self.setFocus()

    # ==================================================================
    # 实时识别模式
    # ==================================================================

    def start_live_inspection(self):
        """启动实时识别任务。

        前置校验：
        - 摄像头必须已启动（live 模式靠摄像头帧推理）；
        - 批量检测和实时识别互斥，同时只能运行一个；

        执行流程：
        1. 先移动到当前料盘的首个槽位原点；
        2. 建立新的 CSV 批次记录并重置所有槽位；
        3. 启动 ``LiveInspectionWorker``，后续槽位由 UI 自动移槽后继续。
        """
        if not self._validate_before_operation(require_motion_params=True):
            return

        if not self.camera_worker or not self.camera_worker.isRunning():
            QMessageBox.warning(self, "错误", "摄像头未启动，无法进入实时识别模式。")
            return

        if (self.worker is not None and self.worker.isRunning()) or \
                (self.live_worker is not None and self.live_worker.isRunning()):
            QMessageBox.warning(self, "提示", "已有任务运行中，请等待完成后再启动实时识别。")
            return
        if self.motion_worker is not None and self.motion_worker.isRunning():
            QMessageBox.warning(self, "提示", "运动任务进行中，请等待完成后再启动实时识别。")
            return

        tray_id = self.tray_combo.currentData()
        try:
            first_coord = self.services.tray_manager.calculate_slot_coordinate(tray_id, 0)
        except ValueError as exc:
            QMessageBox.warning(self, "坐标计算失败", str(exc))
            return

        self.start_btn.setEnabled(False)
        self.live_btn.setEnabled(False)
        self._run_motion_task(
            "正在移动到槽位 1...",
            lambda: self.device_controller.move_to_coordinate(
                first_coord["x"], first_coord["y"], first_coord["z"], self._motion_speed()
            ),
            on_success=lambda _result: self._start_live_worker(tray_id),
            on_failure=self._on_live_start_motion_failed,
            show_success=False,
        )

    def _start_live_worker(self, tray_id):
        """首槽移动到位后启动实时识别线程。"""
        self.services.data_logger.start_new_batch(
            tray_id, expected_slots=len(self.slots),
        )

        for slot in self.slots:
            slot.reset()

        self.live_worker = LiveInspectionWorker(
            engine=self.services.engine,
            camera_worker=self.camera_worker,
            target_m=self.model_input.text(),
            target_a=self.angle_input.text(),
            data_logger=self.services.data_logger,
            total_slots=len(self.slots),
            mode="auto",
        )
        self.live_worker.slot_recognized.connect(self.update_slot_ui)
        self.live_worker.request_move_confirm.connect(self.on_live_request_move_confirm)
        self.live_worker.all_done.connect(self.on_live_all_done)
        self.live_worker.start()

    def _on_live_start_motion_failed(self, result):
        self.start_btn.setEnabled(True)
        self.live_btn.setEnabled(True)
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
            self.live_worker.stop()
            return

        def after_move(_result):
            if self.live_worker is not None:
                self.live_worker.confirm_move()

        self._run_motion_task(
            f"自动移动到槽位 {next_slot_index + 1}...",
            lambda: self.device_controller.move_to_coordinate(
                coord["x"], coord["y"], coord["z"], self._motion_speed()
            ),
            on_success=after_move,
            on_failure=self._on_live_move_failed,
            show_success=False,
        )

    def _on_live_move_failed(self, result):
        if self.live_worker is not None:
            self.live_worker.stop()
        QMessageBox.warning(self, "自动移槽失败", result.message)

    def on_live_all_done(self):
        """实时识别线程全部完成后的回调（在主线程执行）。

        与批量检测 ``on_task_finished`` 对称：保存截图、恢复按钮、弹提示。
        """
        self.live_worker = None
        self.services.data_logger.save_ui_screenshot(self)
        tray_id = self.tray_combo.currentData()
        try:
            origin_coord = self.services.tray_manager.calculate_slot_coordinate(tray_id, 0)
        except ValueError as exc:
            self.start_btn.setEnabled(True)
            self.live_btn.setEnabled(True)
            QMessageBox.warning(self, "回原点失败", str(exc))
            self.setFocus()
            return

        self._run_motion_task(
            "识别完成，正在返回料盘原点...",
            lambda: self.device_controller.move_to_coordinate(
                origin_coord["x"], origin_coord["y"], origin_coord["z"], self._motion_speed()
            ),
            on_success=self._on_live_return_origin_done,
            on_failure=self._on_live_return_origin_failed,
            show_success=False,
        )

    def _on_live_return_origin_done(self, _result):
        self.start_btn.setEnabled(True)
        self.live_btn.setEnabled(True)
        QMessageBox.information(self, "完成", "实时识别已完成，并已返回料盘原点。")
        self.setFocus()

    def _on_live_return_origin_failed(self, result):
        self.start_btn.setEnabled(True)
        self.live_btn.setEnabled(True)
        QMessageBox.warning(self, "回原点失败", f"实时识别已完成，但返回料盘原点失败：{result.message}")
        self.setFocus()

    def check_image_directory(self):
        """启动时尝试恢复上次保存的图像目录。"""
        saved_dir = self.services.config_manager.get_image_directory()
        if saved_dir:
            self.img_dir = saved_dir
            print(f"[✓] 图像目录已加载: {self.img_dir}")
        else:
            print("[!] 图像目录未设置，需要用户手动选择")

    def set_image_directory(self):
        """弹出目录选择器，让用户设置批量检测的图像目录。"""
        print("[DEBUG] set_image_directory 被调用")

        directory = QFileDialog.getExistingDirectory(
            self,
            "选择图像文件夹",
            "",
            QFileDialog.ShowDirsOnly,
        )

        print(f"[DEBUG] 用户选择的目录: {directory}")

        if directory:
            if self.services.config_manager.set_image_directory(directory):
                self.img_dir = directory
                print(f"[✓] 图像目录已保存: {self.img_dir}")
                QMessageBox.information(
                    self,
                    "成功",
                    f"图像目录已设置:\n{directory}\n\n现在可以开始检测了。",
                )
            else:
                print("[✗] 保存目录失败")
                QMessageBox.warning(self, "错误", "无法设置目录，请检查权限！")
        else:
            print("[!] 用户取消了目录选择")

    def keyPressEvent(self, event):
        """全局按键：ESC 关闭窗口，其余交给父类处理。"""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """窗口关闭前确保所有后台线程干净退出，避免进程僵死。"""
        # 停止实时识别（stop() 会 set() 内部 Event，让线程从 wait() 中退出）
        if self.live_worker is not None and self.live_worker.isRunning():
            self.live_worker.stop()
            self.live_worker.wait()
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
