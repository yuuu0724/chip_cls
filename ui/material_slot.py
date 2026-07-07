"""单个料位显示组件。

每块料盘由若干个 ``MaterialSlot`` 组成，代表物理料盘上的一个槽位。
组件只负责显示槽位编号和三色背景：

- default：灰色，未处理
- green：绿色，正确
- red：红色，异常

外部接口保持兼容：``clicked`` 信号、``set_result(status, color_key)``、
``reset()`` 和 ``status_text/color_key`` 状态字段都保留。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout


class MaterialSlot(QFrame):
    """单个料位显示组件。

    Parameters
    ----------
    index : int
        1 基准的料位编号。对外点击信号仍发射 0 基准索引，兼容主窗口现有逻辑。
    """

    clicked = Signal(int)

    _BACKGROUND_COLORS = {
        "green": "#1a7e1a",
        "red": "#c41e1e",
        "default": "#2a2a2e",
    }

    def __init__(self, index, display_index=None):
        super().__init__()
        self.index = index
        self.display_index = display_index if display_index is not None else index
        self.status_text = "待机"
        self.color_key = "default"
        self.init_ui()

    def init_ui(self):
        """构造只显示槽位编号的居中布局。"""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.num_label = QLabel(f"{self.display_index:02d}")
        self.num_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.num_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.num_label.setStyleSheet(
            """
            color: #ffffff;
            border: none;
            background: transparent;
            """
        )

        layout.addWidget(self.num_label, 1, Qt.AlignmentFlag.AlignCenter)
        self.reset()

    def set_result(self, status, color_key):
        """按检测结果刷新背景颜色。

        Parameters
        ----------
        status : str
            外部传入的状态文本。为保持兼容仍写入 ``status_text``，但不再渲染到界面。
        color_key : str
            颜色键，支持 ``green`` / ``red`` / ``default``。未知值回退为灰色。
        """
        self.status_text = status
        self.color_key = color_key if color_key in self._BACKGROUND_COLORS else "default"
        self._apply_background(self.color_key)

    def reset(self):
        """恢复为默认未处理状态。"""
        self.status_text = "待机"
        self.color_key = "default"
        self._apply_background("default")

    def resizeEvent(self, event):
        """根据格子尺寸调整编号字号，保持水平垂直居中。"""
        super().resizeEvent(event)
        self._update_number_font()

    def mousePressEvent(self, event):
        """点击料位时发出 0 基准料位索引，供主窗口确认后执行运动。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index - 1)
        super().mousePressEvent(event)

    def _apply_background(self, color_key):
        bg_color = self._BACKGROUND_COLORS.get(color_key, self._BACKGROUND_COLORS["default"])
        border = "2px solid rgba(255, 255, 255, 0.5)" if color_key == "red" else "1px solid rgba(255, 255, 255, 0.16)"
        self.setStyleSheet(
            f"""
            background-color: {bg_color};
            border: {border};
            border-radius: 8px;
            """
        )
        self._update_number_font()

    def _update_number_font(self):
        side = max(1, min(self.width(), self.height()))
        font_size = max(12, int(side * 0.42))

        font = QFont("Courier New")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(font_size)
        font.setWeight(QFont.Weight.Black)
        self.num_label.setFont(font)
