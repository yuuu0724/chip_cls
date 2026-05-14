"""单个料位显示组件。

每块料盘会由若干个 `MaterialSlot` 组成，代表物理料盘上的一个槽位。
组件内部只管视觉状态（三态：待机 / 绿 / 红），业务判定在
`MaterialController.analyze_status` 里完成。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class MaterialSlot(QFrame):
    """单个料位显示组件。

    Parameters
    ----------
    index : int
        料位编号（1 基准，用于 UI 左上角显示）。内部槽位索引在主窗口和
        日志里统一使用 0 基准，但在用户看到的位置都要 +1。
    """

    clicked = Signal(int)

    def __init__(self, index):
        super().__init__()
        self.index = index
        # 供外部（如 SlotMoveConfirmDialog）读取最近一次识别结果
        self.status_text = "待机"
        self.color_key = "default"
        self.init_ui()

    def init_ui(self):
        """构造内部两行 label：大号编号 + 状态文字。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 槽位编号（两位数字占位，"07" 比 "7" 视觉更稳）
        self.num_label = QLabel(f"{self.index:02d}")
        self.num_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.num_label.setStyleSheet(
            """
            color: #ffffff;
            font-size: 48px;
            font-weight: 900;
            font-family: 'Courier New', 'Courier', monospace;
            border: none;
            background: transparent;
            letter-spacing: 2px;
            """
        )

        # 状态文字（"待机" / "正常" / "方向错误" 等）
        self.status_label = QLabel(" ")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            """
            color: #ffffff;
            font-size: 26px;
            font-weight: 900;
            font-family: 'Microsoft YaHei', '微软雅黑', sans-serif;
            border: none;
            background: transparent;
            line-height: 1.2;
            """
        )

        layout.addWidget(self.num_label)
        layout.addWidget(self.status_label)
        layout.addStretch()

        # 初始状态统一走 reset() 保证样式与文字一致
        self.reset()

    def set_result(self, status, color_key):
        """按检测结果刷新状态文字与背景颜色。

        Parameters
        ----------
        status : str
            中文状态（"正常" / "方向错误" / "型号错误" / "识别失败"）。
        color_key : str
            颜色键，目前支持 ``green`` / ``red`` / ``default`` 三种。
            未识别的颜色键会退回 ``default`` 的深灰。

        视觉规则
        --------
        - green：深绿底，常规细边 -> 正常
        - red：红底 + inset 发光边，更显眼 -> 异常/失败
        - 其他：深灰底，用作"待机"或未知态的 fallback
        """
        bg_colors = {
            "green": "#1a7e1a",
            "red": "#c41e1e",
            "default": "#2a2a2e",
        }

        bg_color = bg_colors.get(color_key, bg_colors["default"])
        self.status_text = status
        self.color_key = color_key
        self.status_label.setText(status)

        if color_key == "red":
            # 红色额外叠一层 inset 发光边，让异常状态一眼可见
            self.setStyleSheet(
                f"""
                background-color: {bg_color};
                border: 2px inset rgba(255, 255, 255, 0.6);
                border-radius: 8px;
                box-shadow: inset 0 0 10px rgba(255, 0, 0, 0.3);
                """
            )
        else:
            self.setStyleSheet(
                f"""
                background-color: {bg_color};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                """
            )

    def reset(self):
        """恢复为默认"待机"态（深灰底，细边）。"""
        self.status_text = "待机"
        self.color_key = "default"
        self.status_label.setText("待机")
        self.setStyleSheet(
            """
            background-color: #2a2a2e;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            """
        )

    def mousePressEvent(self, event):
        """点击槽位时发出 0 基准槽位索引，供主窗口确认后执行运动。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index - 1)
        super().mousePressEvent(event)
