"""主应用窗口 - AI 芯片料盘视觉检测系统"""
import sys
import os
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QGridLayout, 
                               QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
                               QPushButton, QFrame, QFileDialog, QComboBox, QDialog, 
                               QMessageBox, QScrollArea, QTextEdit)
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QPixmap

# 导入项目本地模块（使用绝对导入）
from ocr import OCREngine, TemplateManager, MaterialController
from data import DataLogger, ConfigManager, TrayManager
from workers import CameraWorker, ControlWorker
from .material_slot import MaterialSlot
from .dialogs import TemplateConfirmDialog, CameraCaptureDialog, AddTrayDialog


def _parse_spec(spec_key):
    """将规格键 '3x7' 解析为 (rows, cols)"""
    try:
        r, c = spec_key.split("x")
        return int(r), int(c)
    except (ValueError, AttributeError):
        return 3, 7


# --- 主界面 ---
class OCRApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 芯片料盘视觉检测系统")
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1a1f2e;
            }
            QLabel {
                color: #ffffff;
            }
            QMessageBox QLabel {
                color: #000000;
            }
        """)
        
        # 初始化配置管理器
        self.config_manager = ConfigManager()
        
        # 初始化核心类
        self.engine = OCREngine()
        self.logger = DataLogger()
        self.template_manager = TemplateManager()
        self.tray_manager = TrayManager()
        self.camera_worker = None
        self.slots = []
        self.img_dir = None  # 动态图像目录
        
        self.init_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        # 初始化后检查图像目录
        self.check_image_directory()

    def init_ui(self):
        central = QWidget()
        central.setStyleSheet("background-color: #1a1f2e;")
        self.setCentralWidget(central)
        
        # 主布局 - 水平分割
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # ========== 左侧：料位网格 + 控制区 ==========
        left_layout = QVBoxLayout()
        left_layout.setSpacing(8)
        
        # --- 顶部料盘选择和参数区 ---
        top_control_layout = QHBoxLayout()
        top_control_layout.setSpacing(8)
        
        # 料盘选择
        tray_label = QLabel("料盘:")
        tray_label.setStyleSheet("color: #a1a1a6; font-size: 15px; font-weight: 600;")
        top_control_layout.addWidget(tray_label)
        
        self.tray_combo = QComboBox()
        self.tray_combo.setMaximumWidth(140)
        self.tray_combo.setMinimumHeight(42)
        self.tray_combo.setStyleSheet("""
            QComboBox {
                color: #ffffff;
                background-color: rgba(42, 42, 46, 0.6);
                border: 1px solid rgba(0, 122, 255, 0.2);
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 14px;
                font-weight: 500;
            }
            QComboBox:hover {
                background-color: rgba(53, 53, 57, 0.8);
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a1e;
                color: #ffffff;
                selection-background-color: #007AFF;
            }
        """)
        
        # 加载料盘列表
        for tray_id in self.tray_manager.get_tray_list():
            tray_info = self.tray_manager.get_tray_info(tray_id)
            self.tray_combo.addItem(tray_info["name"], tray_id)
        
        self.tray_combo.currentIndexChanged.connect(self.on_tray_changed)
        top_control_layout.addWidget(self.tray_combo)

        # 新增料盘按钮
        add_tray_btn = QPushButton("＋ 新增料盘")
        add_tray_btn.setMinimumHeight(42)
        add_tray_btn.setStyleSheet("""
            QPushButton {
                color: #007AFF;
                background-color: rgba(0, 122, 255, 0.12);
                border: 1px solid rgba(0, 122, 255, 0.4);
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: rgba(0, 122, 255, 0.22); }
            QPushButton:pressed { background-color: rgba(0, 122, 255, 0.32); }
        """)
        add_tray_btn.clicked.connect(self.add_new_tray)
        top_control_layout.addWidget(add_tray_btn)

        # 型号显示
        model_label = QLabel("型号:")
        model_label.setStyleSheet("color: #a1a1a6; font-size: 15px; font-weight: 600;")
        top_control_layout.addWidget(model_label)
        
        self.model_display = QLabel("ATMLH904")
        self.model_display.setStyleSheet("color: #34C759; font-size: 18px; font-weight: 700;")
        self.model_display.setMinimumWidth(140)
        top_control_layout.addWidget(self.model_display)
        
        # 角度显示
        angle_label = QLabel("角度:")
        angle_label.setStyleSheet("color: #a1a1a6; font-size: 15px; font-weight: 600;")
        top_control_layout.addWidget(angle_label)
        
        self.angle_display = QLabel("90°")
        self.angle_display.setStyleSheet("color: #34C759; font-size: 18px; font-weight: 700;")
        self.angle_display.setMinimumWidth(60)
        top_control_layout.addWidget(self.angle_display)
        
        top_control_layout.addStretch()
        
        left_layout.addLayout(top_control_layout)
        
        # --- 料位网格区（根据料盘规格动态生成）---
        grid_container = QWidget()
        grid_container.setStyleSheet("background-color: #1a1f2e;")
        self.grid_layout = QGridLayout(grid_container)
        self.grid_layout.setSpacing(12)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        first_tray_id = self.tray_combo.currentData()
        spec = self.tray_manager.get_tray_spec(first_tray_id) if first_tray_id else "3x7"
        rows, cols = _parse_spec(spec)
        self._rebuild_grid(rows, cols)

        left_layout.addWidget(grid_container, 1)
        
        # ========== 右侧：三个分区布局 ==========
        right_layout = QVBoxLayout()
        right_layout.setSpacing(8)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # ========== 区域1：摄像头预览区（顶部） ==========
        camera_section = QFrame()
        camera_section.setStyleSheet("""
            QFrame {
                background-color: transparent;
                border: none;
                border-radius: 8px;
                padding: 0px;
            }
        """)
        camera_section_layout = QVBoxLayout(camera_section)
        camera_section_layout.setSpacing(0)
        camera_section_layout.setContentsMargins(0, 0, 0, 0)
        
        # 摄像头预览 - 无缝全填充
        self.camera_frame = QLabel()
        self.camera_frame.setScaledContents(True)
        self.camera_frame.setStyleSheet("""
            background-color: #0a0e1a;
            border: none;
            border-radius: 8px;
        """)
        self.camera_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_frame.setText("无摄像头信号")
        camera_section_layout.addWidget(self.camera_frame, 1)
        
        # 启动摄像头预览
        self.start_camera_preview()
        
        right_layout.addWidget(camera_section, 1)
        
        # ========== 区域2：参数设置区（中间） ==========
        param_section = QFrame()
        param_section.setStyleSheet("""
            QFrame {
                background-color: #1e2a49;
                border: 1px solid rgba(0, 122, 255, 0.4);
                border-radius: 8px;
                padding: 8px;
            }
        """)
        param_section_layout = QVBoxLayout(param_section)
        param_section_layout.setSpacing(8)
        param_section_layout.setContentsMargins(8, 8, 8, 8)
        
        # 区域标题 - '配置中心'
        param_title = QLabel("配置中心")
        param_title.setStyleSheet("color: #007AFF; font-size: 17px; font-weight: 300; letter-spacing: 0.5px;")
        param_section_layout.addWidget(param_title)
        
        # 设置图像目录按钮
        set_img_dir_btn = QPushButton("设置图像目录")
        set_img_dir_btn.setMinimumHeight(50)
        set_img_dir_btn.setStyleSheet("""
            QPushButton {
                background-color: #007AFF;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #0A84FF;
            }
            QPushButton:pressed {
                background-color: #0062CC;
            }
        """)
        set_img_dir_btn.clicked.connect(self.set_image_directory)
        param_section_layout.addWidget(set_img_dir_btn)
        
        # 上传参考图片按钮
        upload_btn = QPushButton("上传参考图片")
        upload_btn.setMinimumHeight(50)
        upload_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9500;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #FFB020;
            }
            QPushButton:pressed {
                background-color: #E68800;
            }
        """)
        upload_btn.clicked.connect(self.upload_reference_image)
        param_section_layout.addWidget(upload_btn)
        
        # 型号编辑
        model_edit_layout = QHBoxLayout()
        model_edit_layout.setSpacing(8)
        model_label = QLabel("型号:")
        model_label.setStyleSheet("color: #a1a1a6; font-size: 15px; font-weight: 500; min-width: 40px;")
        model_edit_layout.addWidget(model_label)
        
        self.model_input = QLineEdit("ATMLH904")
        self.model_input.setMinimumHeight(40)
        self.model_input.setStyleSheet("""
            QLineEdit {
                color: #ffffff;
                background-color: #2a2a2e;
                border: 1px solid #007AFF;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 15px;
                font-weight: 500;
                selection-background-color: #007AFF;
            }
            QLineEdit:focus {
                border: 2px solid #007AFF;
                padding: 7px 11px;
            }
        """)
        model_edit_layout.addWidget(self.model_input)
        param_section_layout.addLayout(model_edit_layout)
        
        # 角度编辑
        angle_edit_layout = QHBoxLayout()
        angle_edit_layout.setSpacing(8)
        angle_label = QLabel("角度:")
        angle_label.setStyleSheet("color: #a1a1a6; font-size: 15px; font-weight: 500; min-width: 40px;")
        angle_edit_layout.addWidget(angle_label)
        
        self.angle_input = QLineEdit("90")
        self.angle_input.setMinimumHeight(40)
        self.angle_input.setStyleSheet("""
            QLineEdit {
                color: #ffffff;
                background-color: #2a2a2e;
                border: 1px solid #007AFF;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 15px;
                font-weight: 500;
                selection-background-color: #007AFF;
            }
            QLineEdit:focus {
                border: 2px solid #007AFF;
                padding: 7px 11px;
            }
        """)
        angle_edit_layout.addWidget(self.angle_input)
        param_section_layout.addLayout(angle_edit_layout)
        
        right_layout.addWidget(param_section, 2)
        
        # ========== 区域3：检测按钮区（底部） ==========
        button_section = QFrame()
        button_section.setStyleSheet("""
            QFrame {
                background-color: #1e2a49;
                border: 1px solid rgba(0, 122, 255, 0.4);
                border-radius: 8px;
                padding: 8px;
            }
        """)
        button_section_layout = QVBoxLayout(button_section)
        button_section_layout.setSpacing(8)
        button_section_layout.setContentsMargins(8, 8, 8, 8)
        
        # 区域标题 - '任务控制'
        button_title = QLabel("任务控制")
        button_title.setStyleSheet("color: #007AFF; font-size: 17px; font-weight: 300; letter-spacing: 0.5px;")
        button_section_layout.addWidget(button_title)
        
        # 启动检测按钮
        self.start_btn = QPushButton("开始检测")
        self.start_btn.setMinimumHeight(72)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                           stop:0 #0D95FF, stop:1 #007AFF);
                color: #ffffff;
                border: 2px solid #0A84FF;
                border-radius: 8px;
                font-size: 19px;
                font-weight: 700;
                letter-spacing: 1.2px;
                box-shadow: 0 0 20px rgba(0, 122, 255, 0.6);
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                           stop:0 #1FA3FF, stop:1 #0A84FF);
                border: 2px solid #0D95FF;
                box-shadow: 0 0 30px rgba(0, 122, 255, 0.8);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                           stop:0 #0062CC, stop:1 #004A99);
                border: 2px solid #003D7A;
            }
            QPushButton:disabled {
                background-color: #5a5a5f;
                color: #8e8e93;
                border: 2px solid #4d4d52;
                box-shadow: none;
            }
        """)
        self.start_btn.clicked.connect(self.run_detection_task)
        button_section_layout.addWidget(self.start_btn)
        
        # 刷新按钮
        refresh_btn = QPushButton("刷新")
        refresh_btn.setMinimumHeight(50)
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(90, 90, 95, 0.6);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover {
                background-color: rgba(99, 99, 104, 0.8);
            }
            QPushButton:pressed {
                background-color: rgba(77, 77, 82, 0.9);
            }
        """)
        refresh_btn.clicked.connect(self.refresh_templates)
        button_section_layout.addWidget(refresh_btn)

        # 访问历史数据按钮
        history_btn = QPushButton("访问历史数据")
        history_btn.setMinimumHeight(50)
        history_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(52, 199, 89, 0.25);
                color: #ffffff;
                border: 1px solid rgba(52, 199, 89, 0.8);
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover {
                background-color: rgba(52, 199, 89, 0.35);
            }
            QPushButton:pressed {
                background-color: rgba(52, 199, 89, 0.45);
            }
        """)
        history_btn.clicked.connect(self.open_history_data)
        button_section_layout.addWidget(history_btn)
        
        # 退出按钮 - 深红色调
        exit_btn = QPushButton("退出")
        exit_btn.setMinimumHeight(50)
        exit_btn.setStyleSheet("""
            QPushButton {
                background-color: #8B4C47;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover {
                background-color: #A55A54;
            }
            QPushButton:pressed {
                background-color: #6B3A36;
            }
        """)
        exit_btn.clicked.connect(self.close)
        button_section_layout.addWidget(exit_btn)
        
        right_layout.addWidget(button_section, 2)
        
        # ========== 将左右布局添加到主布局 ==========
        main_layout.addLayout(left_layout, 3)  # 左侧占75%
        main_layout.addLayout(right_layout, 1)  # 右侧占25%
        
        # 全屏显示
        self.showFullScreen()

    def on_tray_changed(self):
        """料盘切换事件：更新型号/角度 + 重建网格"""
        tray_id = self.tray_combo.currentData()
        if not tray_id:
            return

        model, angle = self.tray_manager.get_tray_model_and_angle(tray_id)
        self.model_display.setText(model or "")
        self.model_input.setText(model or "")
        self.angle_display.setText(f"{angle}°" if angle else "0°")
        self.angle_input.setText(str(angle) if angle else "0")

        spec = self.tray_manager.get_tray_spec(tray_id)
        rows, cols = _parse_spec(spec)
        self._rebuild_grid(rows, cols)

    def _rebuild_grid(self, rows, cols):
        """根据行列数重建料位网格"""
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
        """新增料盘"""
        dialog = AddTrayDialog(self.tray_manager.get_tray_list(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        tray_id = dialog.get_tray_id()
        spec_key = dialog.get_spec_key()
        self.tray_manager.add_tray(
            tray_id,
            name=f"料盘 {tray_id}",
            description="",
            model="",
            angle=0,
            spec=spec_key,
        )

        tray_info = self.tray_manager.get_tray_info(tray_id)
        self.tray_combo.addItem(tray_info["name"], tray_id)
        self.tray_combo.setCurrentIndex(self.tray_combo.count() - 1)

    def start_camera_preview(self):
        """启动摄像头预览"""
        self.camera_worker = CameraWorker(1)
        self.camera_worker.frame_ready.connect(self.update_camera_frame)
        self.camera_worker.start()
    
    def update_camera_frame(self, pixmap):
        """更新摄像头预览画面"""
        scaled_pixmap = pixmap.scaled(160, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.camera_frame.setPixmap(scaled_pixmap)

    def upload_reference_image(self):
        """上传参考图片 - 二选一弹窗：本地文件 / 摄像头拍摄"""
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
        """从本地文件选择参考图片"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择参考图片", "",
            "Image Files (*.png *.jpg *.jpeg *.bmp);;All Files (*)",
        )
        if file_path:
            self._process_reference_image(file_path)

    def _upload_from_camera(self):
        """从摄像头拍摄参考图片"""
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
                    pass

    def _process_reference_image(self, file_path):
        """处理参考图片：OCR 识别 → 确认对话框 → 保存模板"""
        detected_model, detected_angle, success = self.template_manager.add_template_from_image(file_path)

        if success:
            dialog = TemplateConfirmDialog(detected_model, detected_angle, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                final_model = dialog.get_model_name()
                final_angle = dialog.get_angle()

                if final_model != detected_model or final_angle != detected_angle:
                    if detected_model in self.template_manager.templates:
                        self.template_manager.delete_template(detected_model)
                    self.template_manager.templates[final_model] = {
                        "angle": final_angle,
                        "description": "用户从图片手动确认的模板",
                    }
                    self.template_manager.save_templates()

                self.model_input.setText(final_model)
                self.model_display.setText(final_model)
                self.angle_input.setText(str(final_angle))
                self.angle_display.setText(f"{final_angle}°")

                # 同步更新当前料盘的型号和角度
                tray_id = self.tray_combo.currentData()
                if tray_id:
                    self.tray_manager.update_tray(tray_id, model=final_model, angle=final_angle)

                QMessageBox.information(
                    self, "成功",
                    f"模板已保存: {final_model} (角度: {final_angle}°)\n\n现在可以使用此模板进行检测。",
                )
        else:
            error_msg = getattr(self.template_manager, "last_error", "")
            detail = f"\n\n{error_msg}" if error_msg else ""
            QMessageBox.warning(self, "失败", f"无法识别参考图片，请确保图片清晰且包含芯片型号信息{detail}")

    def refresh_templates(self):
        """刷新模板"""
        QMessageBox.information(self, "刷新", "模板已刷新")

    def get_results_directory(self):
        """获取历史结果目录。"""
        logger_dir = os.path.abspath(getattr(self.logger, "base_dir", "results"))
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
        """弹窗显示CSV内容。"""
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
        viewer.setStyleSheet("""
            QTextEdit {
                color: #ffffff;
                background-color: #151b2c;
                border: 1px solid rgba(0, 122, 255, 0.35);
                border-radius: 6px;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 13px;
            }
        """)
        layout.addWidget(viewer)

        dialog.exec()

    def show_image_preview(self, file_path):
        """弹窗显示图片，右上角叠加半透明悬浮操作按钮。"""
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

        # 半透明悬浮按钮容器
        overlay = QWidget()
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        overlay.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(overlay)
        btn_layout.setContentsMargins(0, 10, 10, 0)
        btn_layout.setSpacing(6)

        _btn_style = """
            QPushButton {
                background-color: rgba(0, 0, 0, 0.50);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.25);
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: rgba(0, 0, 0, 0.72); }
            QPushButton:pressed { background-color: rgba(0, 0, 0, 0.85); }
        """

        fullscreen_btn = QPushButton("全屏")
        fullscreen_btn.setStyleSheet(_btn_style)
        def _go_fullscreen():
            dialog.showFullScreen()
            screen = QApplication.primaryScreen().size()
            image_label.setPixmap(_fit_pixmap(screen.width(), screen.height()))
        fullscreen_btn.clicked.connect(_go_fullscreen)
        btn_layout.addWidget(fullscreen_btn)

        shrink_btn = QPushButton("缩小")
        shrink_btn.setStyleSheet(_btn_style)
        def _go_normal():
            dialog.showNormal()
            dialog.resize(960, 640)
            image_label.setPixmap(_fit_pixmap(1600, 1000))
        shrink_btn.clicked.connect(_go_normal)
        btn_layout.addWidget(shrink_btn)

        close_btn = QPushButton("退出")
        close_btn.setStyleSheet(_btn_style)
        close_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(close_btn)

        grid.addWidget(overlay, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        dialog.exec()

    def run_detection_task(self):
        """启动批量检测任务"""
        # 检查图像目录是否已设置
        if not self.img_dir:
            QMessageBox.warning(self, "错误", "请先设置图像目录！\n点击上方'设置图像目录'按钮进行配置。")
            return
        
        # 开始新一轮检测，创建新的结果文件
        tray_id = self.tray_combo.currentData()
        self.logger.start_new_batch(tray_id)
        
        self.start_btn.setEnabled(False)
        for slot in self.slots:
            slot.reset()
        
        target_m = self.model_input.text()
        target_a = self.angle_input.text()

        self.worker = ControlWorker(self.engine, self.img_dir, target_m, target_a, self.logger, total_slots=len(self.slots))
        self.worker.progress_update.connect(self.update_slot_ui)
        self.worker.finished.connect(self.on_task_finished)
        self.worker.start()

    def update_slot_ui(self, index, status, color_key):
        """更新UI显示"""
        if index < len(self.slots):
            self.slots[index].set_result(status, color_key)

    def on_task_finished(self):
        """检测完成"""
        self.start_btn.setEnabled(True)
        QMessageBox.information(self, "完成", "批量检测已完成！")
        self.setFocus()

    def check_image_directory(self):
        """检查图像目录是否已设置"""
        saved_dir = self.config_manager.get_image_directory()
        if saved_dir:
            self.img_dir = saved_dir
            print(f"[✓] 图像目录已加载: {self.img_dir}")
        else:
            print("[!] 图像目录未设置，需要用户手动选择")

    def set_image_directory(self):
        """让用户选择图像目录"""
        print("[DEBUG] set_image_directory 被调用")
        
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择图像文件夹",
            "",
            QFileDialog.ShowDirsOnly
        )
        
        print(f"[DEBUG] 用户选择的目录: {directory}")
        
        if directory:
            if self.config_manager.set_image_directory(directory):
                self.img_dir = directory
                print(f"[✓] 图像目录已保存: {self.img_dir}")
                QMessageBox.information(
                    self,
                    "成功",
                    f"图像目录已设置:\n{directory}\n\n现在可以开始检测了。"
                )
            else:
                print("[✗] 保存目录失败")
                QMessageBox.warning(self, "错误", "无法设置目录，请检查权限！")
        else:
            print("[!] 用户取消了目录选择")

    def keyPressEvent(self, event):
        """按键事件处理"""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """关闭事件处理"""
        if self.camera_worker:
            self.camera_worker.stop()
            self.camera_worker.wait()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OCRApp()
    window.show()
    sys.exit(app.exec())
