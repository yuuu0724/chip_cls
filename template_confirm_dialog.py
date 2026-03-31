"""
模板确认对话框模块
"""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QSpinBox, QPushButton, QTextEdit)


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
