"""单个料位组件模块 - 全背景色填充设计"""
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel
from PySide6.QtCore import Qt


class MaterialSlot(QFrame):
    """单个料位显示组件 - 全背景色填充，支持视觉警示"""
    
    def __init__(self, index):
        super().__init__()
        self.index = index
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 编号标签 - 显著放大（48px）+ 等宽粗体
        self.num_label = QLabel(f"{self.index:02d}")
        self.num_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.num_label.setStyleSheet("""
            color: #ffffff;
            font-size: 48px;
            font-weight: 900;
            font-family: 'Courier New', 'Courier', monospace;
            border: none;
            background: transparent;
            letter-spacing: 2px;
        """)
        
        # 状态标签 - 翻倍文字大小（26px）+ Extra Bold
        self.status_label = QLabel(" ")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("""
            color: #ffffff;
            font-size: 26px;
            font-weight: 900;
            font-family: 'Microsoft YaHei', '微软雅黑', sans-serif;
            border: none;
            background: transparent;
            line-height: 1.2;
        """)
        
        layout.addWidget(self.num_label)
        layout.addWidget(self.status_label)
        layout.addStretch()
        
        self.reset()

    def set_result(self, status, color_key):
        """设置检测结果 - 全背景色填充"""
        # 高饱和度实色映射表
        bg_colors = {
            "green": "#1a7e1a",      # 墨绿色 - 正常
            "red": "#c41e1e",        # 鲜红色 - 错误
            "yellow": "#d99d0f",     # 琥珀色 - 警告
            "default": "#2a2a2e"     # 灰色
        }
        
        bg_color = bg_colors.get(color_key, "#2a2a2e")
        self.status_label.setText(status)
        
        # 错误状态增加视觉警示
        if color_key == "red":
            # 鲜红背景 + 2px 白色内描边
            self.setStyleSheet(f"""
                background-color: {bg_color};
                border: 2px inset rgba(255, 255, 255, 0.6);
                border-radius: 8px;
                box-shadow: inset 0 0 10px rgba(255, 0, 0, 0.3);
            """)
        elif color_key == "green":
            # 墨绿背景 + 细微边框
            self.setStyleSheet(f"""
                background-color: {bg_color};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
            """)
        elif color_key == "yellow":
            # 琥珀色背景
            self.setStyleSheet(f"""
                background-color: {bg_color};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
            """)
        else:
            # 默认灰色
            self.setStyleSheet(f"""
                background-color: {bg_color};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
            """)

    def reset(self):
        """重置料位状态 - 待机状态（深灰色背景）"""
        self.status_label.setText("待机")
        self.setStyleSheet("""
            background-color: #2a2a2e;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
        """)
