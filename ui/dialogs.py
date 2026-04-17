"""对话框组件模块"""
import os
import tempfile

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QSpinBox, QPushButton, QTextEdit,
                               QComboBox, QMessageBox)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

TRAY_SPEC_PRESETS = [
    {"label": "3×7 (21槽)", "rows": 3, "cols": 7, "key": "3x7"},
    {"label": "4×6 (24槽)", "rows": 4, "cols": 6, "key": "4x6"},
    {"label": "2×10 (20槽)", "rows": 2, "cols": 10, "key": "2x10"},
]


class TemplateConfirmDialog(QDialog):
    """确认识别结果的对话框"""
    
    def __init__(self, detected_model, detected_angle, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认模板参数")
        self.setFixedSize(500, 350)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1e;
            }
            QLabel {
                color: #ffffff;
            }
        """)
        
        layout = QVBoxLayout()
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 标题
        title = QLabel("确认模板参数")
        title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        
        # 说明文字
        tips = QLabel("⚠️  下面输入的【模板名称】将用于后续检测时的文本匹配。请确保准确性！")
        tips.setStyleSheet("color: #FFD60A; font-size: 13px; margin-top: 6px; line-height: 1.5;")
        tips.setWordWrap(True)
        layout.addWidget(tips)
        
        # 显示识别到的原始文本
        raw_text_label = QLabel("识别到的原始文本:")
        raw_text_label.setStyleSheet("color: #a1a1a6; font-size: 13px; margin-top: 8px;")
        layout.addWidget(raw_text_label)
        
        self.raw_text_display = QTextEdit()
        self.raw_text_display.setText(detected_model)
        self.raw_text_display.setReadOnly(True)
        self.raw_text_display.setMaximumHeight(50)
        self.raw_text_display.setStyleSheet("""
            QTextEdit {
                color: #34C759;
                background-color: #2a2a2e;
                border: 1px solid #444449;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
            }
        """)
        layout.addWidget(self.raw_text_display)
        
        # 模板名称输入（用于后续匹配）
        model_label = QLabel("模板名称 (用于检测匹配):")
        model_label.setStyleSheet("color: #a1a1a6; font-size: 13px; margin-top: 8px;")
        layout.addWidget(model_label)
        
        self.model_input = QLineEdit(detected_model)
        self.model_input.setMinimumHeight(36)
        self.model_input.setStyleSheet("""
            QLineEdit {
                color: #ffffff;
                background-color: #2a2a2e;
                border: 1px solid #444449;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: 600;
                selection-background-color: #007AFF;
            }
            QLineEdit:focus {
                border: 2px solid #007AFF;
                padding: 7px 11px;
            }
        """)
        layout.addWidget(self.model_input)
        
        # 显示识别到的角度
        angle_label = QLabel("识别到的角度 (度):")
        angle_label.setStyleSheet("color: #a1a1a6; font-size: 13px; margin-top: 8px;")
        layout.addWidget(angle_label)
        
        self.angle_spinbox = QSpinBox()
        self.angle_spinbox.setRange(0, 359)
        self.angle_spinbox.setValue(detected_angle)
        self.angle_spinbox.setMinimumHeight(36)
        self.angle_spinbox.setStyleSheet("""
            QSpinBox {
                color: #ffffff;
                background-color: #2a2a2e;
                border: 1px solid #444449;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: 600;
            }
            QSpinBox:focus {
                border: 2px solid #007AFF;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background: transparent;
                border: none;
            }
        """)
        layout.addWidget(self.angle_spinbox)
        
        layout.addSpacing(8)
        
        # 按钮 - 苹果风格
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(40)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #5a5a5f;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover {
                background-color: #636368;
            }
            QPushButton:pressed {
                background-color: #4d4d52;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        ok_btn = QPushButton("保存模板")
        ok_btn.setFixedHeight(40)
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #34C759;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover {
                background-color: #31DC74;
            }
            QPushButton:pressed {
                background-color: #2BA84B;
            }
        """)
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def get_model_name(self):
        """获取模板名称"""
        return self.model_input.text()
    
    def get_angle(self):
        """获取角度"""
        return self.angle_spinbox.value()


class CameraCaptureDialog(QDialog):
    """摄像头拍摄参考图片对话框，复用已有的 CameraWorker"""

    def __init__(self, camera_worker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("摄像头拍摄参考图片")
        self.setFixedSize(660, 560)
        self.setStyleSheet("""
            QDialog { background-color: #1a1a1e; }
            QLabel { color: #ffffff; }
        """)
        self.camera_worker = camera_worker
        self.captured_path = None
        self._last_pixmap = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        tip = QLabel("将芯片对准摄像头，点击「拍摄」获取参考图片")
        tip.setStyleSheet("color: #a1a1a6; font-size: 14px;")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tip)

        self.preview_label = QLabel("等待摄像头信号…")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(620, 440)
        self.preview_label.setScaledContents(False)
        self.preview_label.setStyleSheet(
            "background-color: #0a0e1a; border: 1px solid rgba(0,122,255,0.3); border-radius: 8px;"
        )
        layout.addWidget(self.preview_label, 1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(44)
        cancel_btn.setStyleSheet("""
            QPushButton { background-color: #5a5a5f; color: #fff; border: none; border-radius: 8px; font-size: 15px; font-weight: 600; }
            QPushButton:hover { background-color: #636368; }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        capture_btn = QPushButton("拍摄")
        capture_btn.setFixedHeight(44)
        capture_btn.setStyleSheet("""
            QPushButton { background-color: #FF9500; color: #fff; border: none; border-radius: 8px; font-size: 15px; font-weight: 600; }
            QPushButton:hover { background-color: #FFB020; }
            QPushButton:pressed { background-color: #E68800; }
        """)
        capture_btn.clicked.connect(self._capture)
        btn_layout.addWidget(capture_btn)

        layout.addLayout(btn_layout)

        if self.camera_worker:
            self.camera_worker.frame_ready.connect(self._on_frame)

    def _on_frame(self, pixmap):
        self._last_pixmap = pixmap
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def _capture(self):
        if not self._last_pixmap:
            return
        self.captured_path = os.path.join(tempfile.gettempdir(), "ocr_ref_capture.png")
        self._last_pixmap.save(self.captured_path)
        self.accept()

    def get_captured_path(self):
        return self.captured_path

    def done(self, result):
        if self.camera_worker:
            try:
                self.camera_worker.frame_ready.disconnect(self._on_frame)
            except RuntimeError:
                pass
        super().done(result)


class AddTrayDialog(QDialog):
    """新增料盘对话框"""

    _FIELD_STYLE = """
        color: #ffffff;
        background-color: #2a2a2e;
        border: 1px solid #444449;
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 14px;
        font-weight: 500;
    """
    _LABEL_STYLE = "color: #a1a1a6; font-size: 13px; margin-top: 6px;"

    def __init__(self, existing_ids, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新增料盘")
        self.setFixedSize(420, 300)
        self.setStyleSheet("QDialog { background-color: #1a1a1e; } QLabel { color: #ffffff; }")
        self.existing_ids = existing_ids

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("新增料盘")
        title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        # 料盘编号
        id_label = QLabel("料盘编号（唯一）:")
        id_label.setStyleSheet(self._LABEL_STYLE)
        layout.addWidget(id_label)

        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("例如: A0004")
        self.id_input.setMinimumHeight(38)
        self.id_input.setStyleSheet(self._FIELD_STYLE)
        layout.addWidget(self.id_input)

        # 料盘规格
        spec_label = QLabel("料盘规格:")
        spec_label.setStyleSheet(self._LABEL_STYLE)
        layout.addWidget(spec_label)

        self.spec_combo = QComboBox()
        self.spec_combo.setMinimumHeight(38)
        self.spec_combo.setStyleSheet(self._FIELD_STYLE + """
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #1a1a1e; color: #ffffff;
                selection-background-color: #007AFF;
            }
        """)
        for preset in TRAY_SPEC_PRESETS:
            self.spec_combo.addItem(preset["label"], preset["key"])
        layout.addWidget(self.spec_combo)

        layout.addStretch()

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(40)
        cancel_btn.setStyleSheet("""
            QPushButton { background-color: #5a5a5f; color: #fff; border: none; border-radius: 8px; font-weight: 600; font-size: 15px; }
            QPushButton:hover { background-color: #636368; }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("确认新增")
        ok_btn.setFixedHeight(40)
        ok_btn.setStyleSheet("""
            QPushButton { background-color: #007AFF; color: #fff; border: none; border-radius: 8px; font-weight: 600; font-size: 15px; }
            QPushButton:hover { background-color: #0A84FF; }
        """)
        ok_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

    def _on_confirm(self):
        tray_id = self.id_input.text().strip()
        if not tray_id:
            QMessageBox.warning(self, "提示", "请输入料盘编号")
            return
        if tray_id in self.existing_ids:
            QMessageBox.warning(self, "提示", f"编号 {tray_id} 已存在，请使用其他编号")
            return
        self.accept()

    def get_tray_id(self):
        return self.id_input.text().strip()

    def get_spec_key(self):
        return self.spec_combo.currentData()
