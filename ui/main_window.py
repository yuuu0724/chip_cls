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

from PySide6.QtCore import Qt
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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from data import AppServices
from ocr import MaterialController
from workers import CameraWorker, ControlWorker, LiveInspectionWorker, RS232Interface

from . import styles as S
from .dialogs import AddTrayDialog, CameraCaptureDialog, SlotMoveConfirmDialog, TemplateConfirmDialog
from .material_slot import MaterialSlot


def _parse_spec(spec_key):
    """将料盘规格键解析为 `(rows, cols)`。

    Parameters
    ----------
    spec_key : str | None
        形如 ``"3x7"`` / ``"4x6"`` 的规格字符串。

    Returns
    -------
    tuple[int, int]
        `(行数, 列数)`。若解析失败返回默认的 `(3, 7)`。
    """
    try:
        r, c = spec_key.split("x")
        return int(r), int(c)
    except (ValueError, AttributeError):
        return 3, 7


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

        # UI 相关状态
        self.camera_worker = None   # 摄像头预览线程（懒启动）
        self.worker = None          # 正在运行的批量检测线程（None 表示空闲）
        self.live_worker = None     # 正在运行的实时识别线程（None 表示空闲）
        self.slots = []             # 当前料盘的槽位组件列表
        self.img_dir = None         # 用户选择的图像目录

        self.init_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 启动后立刻读取历史配置里的图像目录（若已保存过）
        self.check_image_directory()

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
            tray_info = self.services.tray_manager.get_tray_info(tray_id)
            self.tray_combo.addItem(tray_info["name"], tray_id)

        self.tray_combo.currentIndexChanged.connect(self.on_tray_changed)
        top_control_layout.addWidget(self.tray_combo)

        # 新增料盘按钮
        add_tray_btn = QPushButton("＋ 新增料盘")
        add_tray_btn.setMinimumHeight(42)
        add_tray_btn.setStyleSheet(S.ADD_TRAY_BTN)
        add_tray_btn.clicked.connect(self.add_new_tray)
        top_control_layout.addWidget(add_tray_btn)

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
        spec = (
            self.services.tray_manager.get_tray_spec(first_tray_id)
            if first_tray_id else "3x7"
        )
        rows, cols = _parse_spec(spec)
        self._rebuild_grid(rows, cols)

        left_layout.addWidget(grid_container, 1)

        # ========== 右侧：摄像头 + 配置中心 + 任务控制 ==========
        right_layout = QVBoxLayout()
        right_layout.setSpacing(8)
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
        param_section_layout.setSpacing(8)
        param_section_layout.setContentsMargins(8, 8, 8, 8)

        # 分区标题
        param_title = QLabel("配置中心")
        param_title.setStyleSheet(S.SECTION_TITLE)
        param_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        param_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        param_section_layout.addWidget(param_title)

        # 设置图像目录（首次进入必须配置，否则开始检测会被拒绝）
        set_img_dir_btn = QPushButton("设置图像目录")
        set_img_dir_btn.setMinimumHeight(50)
        set_img_dir_btn.setStyleSheet(S.PRIMARY_BUTTON)
        set_img_dir_btn.clicked.connect(self.set_image_directory)
        param_section_layout.addWidget(set_img_dir_btn)

        # 上传参考图片（本地文件 / 摄像头拍摄，二选一）
        upload_btn = QPushButton("上传参考图片")
        upload_btn.setMinimumHeight(50)
        upload_btn.setStyleSheet(S.WARNING_BUTTON)
        upload_btn.clicked.connect(self.upload_reference_image)
        param_section_layout.addWidget(upload_btn)

        # 型号编辑行：label + 输入框，供用户手动覆写当前批次的目标型号
        model_edit_layout = QHBoxLayout()
        model_edit_layout.setSpacing(8)
        model_label = QLabel("型号:")
        model_label.setStyleSheet(S.PARAM_LABEL)
        model_edit_layout.addWidget(model_label)

        self.model_input = QLineEdit("ATMLH904")
        self.model_input.setMinimumHeight(40)
        self.model_input.setStyleSheet(S.PARAM_INPUT)
        model_edit_layout.addWidget(self.model_input)
        param_section_layout.addLayout(model_edit_layout)

        # 角度编辑行：同上，目标角度
        angle_edit_layout = QHBoxLayout()
        angle_edit_layout.setSpacing(8)
        angle_label = QLabel("角度:")
        angle_label.setStyleSheet(S.PARAM_LABEL)
        angle_edit_layout.addWidget(angle_label)

        self.angle_input = QLineEdit("90")
        self.angle_input.setMinimumHeight(40)
        self.angle_input.setStyleSheet(S.PARAM_INPUT)
        angle_edit_layout.addWidget(self.angle_input)
        param_section_layout.addLayout(angle_edit_layout)

        right_layout.addWidget(param_section, 1)
        
        # ---------- 区域 3：任务控制 ----------
        button_section = QFrame()
        button_section.setStyleSheet(S.SECTION_FRAME)
        button_section_layout = QVBoxLayout(button_section)
        button_section_layout.setSpacing(8)
        button_section_layout.setContentsMargins(8, 8, 8, 8)

        button_title = QLabel("任务控制")
        button_title.setStyleSheet(S.SECTION_TITLE)
        button_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        button_section_layout.addWidget(button_title)

        # 启动检测：整个界面最醒目的入口
        self.start_btn = QPushButton("开始检测")
        self.start_btn.setMinimumHeight(72)
        self.start_btn.setStyleSheet(S.START_BUTTON)
        self.start_btn.clicked.connect(self.run_detection_task)
        button_section_layout.addWidget(self.start_btn)

        # 实时识别：摄像头逐槽位采集模式
        live_row = QHBoxLayout()
        live_row.setSpacing(6)

        self.live_btn = QPushButton("实时识别")
        self.live_btn.setMinimumHeight(50)
        self.live_btn.setStyleSheet(S.LIVE_BUTTON)
        self.live_btn.clicked.connect(self.start_live_inspection)
        live_row.addWidget(self.live_btn, 3)

        # 模式选择下拉（手动/自动预留），与按钮并排
        self.live_mode_combo = QComboBox()
        self.live_mode_combo.setMinimumHeight(50)
        self.live_mode_combo.setStyleSheet(S.LIVE_MODE_COMBO)
        self.live_mode_combo.addItem("手动", "manual")
        self.live_mode_combo.addItem("自动(预留)", "auto")
        live_row.addWidget(self.live_mode_combo, 2)

        button_section_layout.addLayout(live_row)

        # 刷新：重置所有槽位到"待机"
        refresh_btn = QPushButton("刷新")
        refresh_btn.setMinimumHeight(50)
        refresh_btn.setStyleSheet(S.REFRESH_BUTTON)
        refresh_btn.clicked.connect(self.refresh_templates)
        button_section_layout.addWidget(refresh_btn)

        # 访问历史数据：CSV 与截图的文件选择器
        history_btn = QPushButton("访问历史数据")
        history_btn.setMinimumHeight(50)
        history_btn.setStyleSheet(S.HISTORY_BUTTON)
        history_btn.clicked.connect(self.open_history_data)
        button_section_layout.addWidget(history_btn)

        # 退出：同 closeEvent，清理摄像头线程
        exit_btn = QPushButton("退出")
        exit_btn.setMinimumHeight(50)
        exit_btn.setStyleSheet(S.EXIT_BUTTON)
        exit_btn.clicked.connect(self.close)
        button_section_layout.addWidget(exit_btn)

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

        spec = self.services.tray_manager.get_tray_spec(tray_id)
        rows, cols = _parse_spec(spec)
        self._rebuild_grid(rows, cols)

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
            self.grid_layout.addWidget(slot, i // cols, i % cols)
            self.slots.append(slot)

    def add_new_tray(self):
        """弹出"新增料盘"对话框并把结果写回 tray_manager。

        用户点击确认后：
        1. 以 `料盘 {tray_id}` 为默认名写入配置
        2. 追加到下拉末尾并切换过去（触发 `on_tray_changed` 重建网格）
        """
        dialog = AddTrayDialog(self.services.tray_manager.get_tray_list(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        tray_id = dialog.get_tray_id()
        spec_key = dialog.get_spec_key()
        self.services.tray_manager.add_tray(
            tray_id,
            name=f"料盘 {tray_id}",
            description="",
            model="",
            angle=0,
            spec=spec_key,
        )

        tray_info = self.services.tray_manager.get_tray_info(tray_id)
        self.tray_combo.addItem(tray_info["name"], tray_id)
        self.tray_combo.setCurrentIndex(self.tray_combo.count() - 1)

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

        tray_info = self.services.tray_manager.get_tray_info(tray_id)
        name = tray_info["name"] if tray_info else tray_id
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除 {name} (编号 {tray_id}) 吗？此操作不可撤销。",
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

    def start_camera_preview(self):
        """启动摄像头预览线程。

        默认打开 camera_id=1（通常是外接相机）。若打开失败，`CameraWorker`
        内部会悄悄结束 run()，UI 将保持"无摄像头信号"占位。
        """
        self.camera_worker = CameraWorker(1)
        self.camera_worker.frame_ready.connect(self.update_camera_frame)
        self.camera_worker.start()

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

    def _process_reference_image(self, file_path):
        """处理参考图片。

        流程：
        1. 调 `template_manager.add_template_from_image` 做 OCR，拿到
           `(detected_model, detected_angle, success)`。
        2. 成功则弹 `TemplateConfirmDialog` 让用户校对型号/角度。
        3. 用户若改过参数，就把旧模板删掉、保存新模板。
        4. 同步更新 UI 和当前料盘里的 model/angle 字段。
        """
        detected_model, detected_angle, success = \
            self.services.template_manager.add_template_from_image(file_path)

        if success:
            dialog = TemplateConfirmDialog(detected_model, detected_angle, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                final_model = dialog.get_model_name()
                final_angle = dialog.get_angle()

                # 用户修改了参数 -> 覆盖保存
                if final_model != detected_model or final_angle != detected_angle:
                    if detected_model in self.services.template_manager.templates:
                        self.services.template_manager.delete_template(detected_model)
                    self.services.template_manager.templates[final_model] = {
                        "angle": final_angle,
                        "description": "用户从图片手动确认的模板",
                    }
                    self.services.template_manager.save_templates()

                # 同步 UI（两套：只读显示 + 可编辑输入）
                self.model_input.setText(final_model)
                self.model_display.setText(final_model)
                self.angle_input.setText(str(final_angle))
                self.angle_display.setText(f"{final_angle}°")

                # 把新模板挂到当前料盘上，下次切换料盘时自动恢复
                tray_id = self.tray_combo.currentData()
                if tray_id:
                    self.services.tray_manager.update_tray(
                        tray_id, model=final_model, angle=final_angle,
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
        1. 建立新的 CSV 批次记录；
        2. 重置所有槽位为待机；
        3. 启动 ``LiveInspectionWorker`` 线程，第一个槽位直接开始识别，
           后续每个槽位等待工人点击"确认已就位"。
        """
        if not self.camera_worker or not self.camera_worker.isRunning():
            QMessageBox.warning(self, "错误", "摄像头未启动，无法进入实时识别模式。")
            return

        if (self.worker is not None and self.worker.isRunning()) or \
                (self.live_worker is not None and self.live_worker.isRunning()):
            QMessageBox.warning(self, "提示", "已有任务运行中，请等待完成后再启动实时识别。")
            return

        tray_id = self.tray_combo.currentData()
        self.services.data_logger.start_new_batch(
            tray_id, expected_slots=len(self.slots),
        )

        # 禁用两个启动按钮，防止重入
        self.start_btn.setEnabled(False)
        self.live_btn.setEnabled(False)
        self.live_mode_combo.setEnabled(False)

        for slot in self.slots:
            slot.reset()

        mode = self.live_mode_combo.currentData()

        self.live_worker = LiveInspectionWorker(
            engine=self.services.engine,
            camera_worker=self.camera_worker,
            target_m=self.model_input.text(),
            target_a=self.angle_input.text(),
            data_logger=self.services.data_logger,
            total_slots=len(self.slots),
            rs232=None,   # RS232 接口预留，暂不接入
            mode=mode,
        )
        self.live_worker.slot_recognized.connect(self.update_slot_ui)
        self.live_worker.request_move_confirm.connect(self.on_live_request_move_confirm)
        self.live_worker.all_done.connect(self.on_live_all_done)
        self.live_worker.start()

    def on_live_request_move_confirm(self, next_slot_index):
        """实时识别线程发出"请移至下一槽位"请求时在主线程弹出确认对话框。

        - 用户点"确认已就位" → accept → 调 ``live_worker.confirm_move()``；
        - 用户点"停止实时识别" → reject → 调 ``live_worker.stop()``。

        Parameters
        ----------
        next_slot_index : int
            下一个待识别的槽位索引（0 基准）；对话框显示时转成 1 基准。
        """
        # next_slot_index 是 0 基准的"下一个"槽位；刚识别完的是它的前一个
        done_index = next_slot_index - 1   # 0 基准，刚识别完的槽位
        if 0 <= done_index < len(self.slots):
            slot_widget = self.slots[done_index]
            current_status = slot_widget.status_text or "—"
            current_color = slot_widget.color_key or "default"
        else:
            current_status = "—"
            current_color = "default"

        dialog = SlotMoveConfirmDialog(
            current_slot=next_slot_index,        # 刚完成的槽位（1基准）
            next_slot=next_slot_index + 1,       # 下一个槽位（1基准）
            current_status=current_status,
            current_color=current_color,
            total_slots=len(self.slots),
            parent=self,
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            # 工人确认就位，解除工作线程阻塞
            self.live_worker.confirm_move()
        else:
            # 工人主动停止
            self.live_worker.stop()

    def on_live_all_done(self):
        """实时识别线程全部完成后的回调（在主线程执行）。

        与批量检测 ``on_task_finished`` 对称：保存截图、恢复按钮、弹提示。
        """
        self.services.data_logger.save_ui_screenshot(self)
        self.start_btn.setEnabled(True)
        self.live_btn.setEnabled(True)
        self.live_mode_combo.setEnabled(True)
        self.live_worker = None
        QMessageBox.information(self, "完成", "实时识别已完成！")
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
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OCRApp()
    window.show()
    sys.exit(app.exec())
