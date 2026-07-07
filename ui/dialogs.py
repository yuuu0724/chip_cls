"""对话框组件模块。

集中管理 UI 层所有次级弹窗：

- :class:`TemplateConfirmDialog` —— OCR 识别出参考芯片型号后，让用户确认/修改。
- :class:`CameraCaptureDialog` —— 实时预览摄像头并"咔嚓"抓一帧当参考图。
- :class:`VirtualKeyboardDialog` —— 触屏场景下没有物理键盘时的数字+字母+标点软键盘。
- :class:`AddTrayDialog` —— 新增料盘时录入名称和规格。

料盘规格在这里有两个来源：
``TRAY_SPEC_PRESETS`` 提供三种常见预设，用户也可以选"自定义规格"
通过行数/列数自由组合。``CUSTOM_TRAY_SPEC_KEY`` 是 QComboBox 中用于
区分"选了预设"还是"选了自定义"的哨兵键。
"""

import os
import tempfile

from motion import pulses_to_mm, x_mm_to_pulses, y_mm_to_pulses, z_mm_to_pulses
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# 预置料盘规格：产品常见的三种托盘尺寸。保持顺序，首项默认选中。
TRAY_SPEC_PRESETS = [
    {"label": "3x7 (21槽)", "rows": 3, "cols": 7, "key": "3x7"},
    {"label": "4x6 (24槽)", "rows": 4, "cols": 6, "key": "4x6"},
    {"label": "2x10 (20槽)", "rows": 2, "cols": 10, "key": "2x10"},
]
# "自定义规格" 选项的 data 值；选到它就把 SpinBox 行/列显示出来。
CUSTOM_TRAY_SPEC_KEY = "__custom__"


def _build_spec_key(rows, cols):
    """把 (行, 列) 拼成存盘用的 key，例如 ``3x7``。"""
    return f"{rows}x{cols}"


def _build_spec_label(rows, cols):
    """构造给用户看的规格标签，例如 ``3x7 (21槽)``。"""
    return f"{rows}x{cols} ({rows * cols}槽)"


class TemplateConfirmDialog(QDialog):
    """确认模板参数对话框，支持从 OCR 结果中选择标准芯片型号。"""

    def __init__(
        self,
        detected_model,
        detected_angle,
        detected_texts=None,
        existing_models=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("确认模板参数")
        self.setFixedSize(560, 560)
        self.setStyleSheet(
            """
            QDialog { background-color: #1a1a1e; }
            QLabel { color: #ffffff; }
            """
        )
        self.detected_texts = list(detected_texts or [])

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("确认模板参数")
        title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        tips = QLabel("请选择当前模板对应的标准芯片型号；下拉框仅包含本次 OCR 识别结果。")
        tips.setStyleSheet("color: #FFD60A; font-size: 13px; line-height: 1.5;")
        tips.setWordWrap(True)
        layout.addWidget(tips)

        raw_text_label = QLabel("OCR 识别结果（按识别顺序）:")
        raw_text_label.setStyleSheet("color: #a1a1a6; font-size: 13px; margin-top: 6px;")
        layout.addWidget(raw_text_label)

        self.raw_text_display = QTextEdit()
        self.raw_text_display.setPlainText("\n".join(self.detected_texts) or "未识别到文本")
        self.raw_text_display.setReadOnly(True)
        self.raw_text_display.setMaximumHeight(140)
        self.raw_text_display.setStyleSheet(
            """
            QTextEdit {
                color: #34C759;
                background-color: #2a2a2e;
                border: 1px solid #444449;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
            }
            """
        )
        layout.addWidget(self.raw_text_display)

        model_label = QLabel("模板型号:")
        model_label.setStyleSheet("color: #a1a1a6; font-size: 13px; margin-top: 6px;")
        layout.addWidget(model_label)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)
        self.model_combo.setMinimumHeight(38)
        self.model_combo.setStyleSheet(
            """
            QComboBox, QComboBox QLineEdit {
                color: #ffffff;
                background-color: #2a2a2e;
                border: 1px solid #444449;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: 600;
                selection-background-color: #007AFF;
            }
            QComboBox {
                padding-right: 36px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 34px;
                border-left: 1px solid #444449;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
                background-color: #3a3a3f;
            }
            QComboBox::drop-down:hover {
                background-color: #4a4a50;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0px;
                height: 0px;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 7px solid #ffffff;
                margin-right: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a1e;
                color: #ffffff;
                border: 1px solid #444449;
                selection-background-color: #007AFF;
            }
            """
        )
        seen = set()
        for text in self.detected_texts:
            text = str(text).strip()
            if text and text not in seen:
                self.model_combo.addItem(text)
                seen.add(text)
        if detected_model and detected_model in seen:
            self.model_combo.setCurrentText(detected_model)
        elif self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(0)
        layout.addWidget(self.model_combo)

        angle_label = QLabel("识别到的角度:")
        angle_label.setStyleSheet("color: #a1a1a6; font-size: 13px; margin-top: 6px;")
        layout.addWidget(angle_label)

        self.angle_spinbox = QSpinBox()
        self.angle_spinbox.setRange(0, 359)
        self.angle_spinbox.setValue(int(detected_angle or 0))
        self.angle_spinbox.setMinimumHeight(36)
        self.angle_spinbox.setStyleSheet(self._spin_style())
        layout.addWidget(self.angle_spinbox)

        layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(40)
        cancel_btn.setStyleSheet(self._cancel_button_style())
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("保存模板")
        ok_btn.setFixedHeight(40)
        ok_btn.setStyleSheet(self._ok_button_style())
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

    def accept(self):
        """保存前必须选择或输入模板型号。"""
        if not self.get_model_name():
            QMessageBox.warning(self, "提示", "请选择或输入模板型号后再保存。")
            return
        super().accept()

    def get_model_name(self):
        """用户最终确认 / 修改后的模板名（保存到 templates.json 的 key）。"""
        return self.model_combo.currentText().strip()

    def get_angle(self):
        """用户最终确认 / 修改后的角度（0~359 整数）。"""
        return self.angle_spinbox.value()

    @staticmethod
    def _spin_style():
        return """
            QSpinBox {
                color: #ffffff;
                background-color: #2a2a2e;
                border: 1px solid #444449;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: 600;
            }
            QSpinBox:focus { border: 2px solid #007AFF; }
            QSpinBox::up-button, QSpinBox::down-button { background: transparent; border: none; }
        """

    @staticmethod
    def _secondary_button_style():
        return """
            QPushButton {
                background-color: #444449;
                color: #ffffff;
                border: 1px solid #555559;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #55555a; }
        """

    @staticmethod
    def _cancel_button_style():
        return """
            QPushButton {
                background-color: #5a5a5f;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover { background-color: #636368; }
        """

    @staticmethod
    def _ok_button_style():
        return """
            QPushButton {
                background-color: #34C759;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover { background-color: #31DC74; }
            QPushButton:pressed { background-color: #2BA84B; }
        """


class CameraCaptureDialog(QDialog):
    """摄像头拍摄参考图对话框，复用已有 CameraWorker。

    设计上不自己启停摄像头线程，而是复用主窗口已运行的 `CameraWorker`：
    - 避免同一路摄像头被两处同时打开（Windows 上 DSHOW 后端会直接报占用）；
    - 关闭对话框时记得 disconnect，不然 worker 会继续往一个已销毁的 QLabel 发帧。

    拍摄结果先落到系统临时目录（``%TEMP%/ocr_ref_capture.png``），
    后续调用方可以用这张图去做 OCR / 保存模板。

    Parameters
    ----------
    camera_worker : CameraWorker | None
        主窗口创建的摄像头线程；为 None 时对话框打不开实时画面，只做 placeholder。
    parent : QWidget | None
        父控件。
    """

    def __init__(self, camera_worker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("摄像头拍摄参考图片")
        self.setFixedSize(660, 560)
        self.setStyleSheet(
            """
            QDialog { background-color: #1a1a1e; }
            QLabel { color: #ffffff; }
            """
        )
        self.camera_worker = camera_worker
        self.captured_path = None
        self._last_pixmap = None
        self._last_frame_bgr = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        tip = QLabel("将芯片对准摄像头，点击“拍摄”获取参考图片。")
        tip.setStyleSheet("color: #a1a1a6; font-size: 14px;")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tip)

        self.preview_label = QLabel("等待摄像头信号...")
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
        cancel_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #5a5a5f;
                color: #fff;
                border: none;
                border-radius: 8px;
                font-size: 15px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #636368; }
            """
        )
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        capture_btn = QPushButton("拍摄")
        capture_btn.setFixedHeight(44)
        capture_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #FF9500;
                color: #fff;
                border: none;
                border-radius: 8px;
                font-size: 15px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #FFB020; }
            QPushButton:pressed { background-color: #E68800; }
            """
        )
        capture_btn.clicked.connect(self._capture)
        btn_layout.addWidget(capture_btn)

        layout.addLayout(btn_layout)

        if self.camera_worker:
            self.camera_worker.frame_ready.connect(self._on_frame)

    def _on_frame(self, pixmap, frame_bgr=None):
        """CameraWorker 的 `frame_ready` 槽。

        保留与预览 pixmap 同帧的 BGR 图，拍摄时直接落盘。
        展示时再按 preview_label 的大小做保形缩放。
        """
        self._last_pixmap = pixmap
        if frame_bgr is not None:
            self._last_frame_bgr = frame_bgr.copy()
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def _capture(self):
        """按下"拍摄"按钮：把当前最后一帧原图落盘到临时路径并关闭对话框。"""
        if self._last_frame_bgr is None and not self._last_pixmap:
            return
        self.captured_path = os.path.join(tempfile.gettempdir(), "ocr_ref_capture.png")
        if self._last_frame_bgr is not None:
            import cv2

            cv2.imwrite(self.captured_path, self._last_frame_bgr)
        else:
            self._last_pixmap.save(self.captured_path)
        self.accept()

    def get_captured_path(self):
        """返回拍摄好的图片路径；用户取消时为 None。"""
        return self.captured_path

    def done(self, result):
        """重写 QDialog.done，确保对话框关闭前先断开信号。

        如果不 disconnect，worker 线程会继续往 preview_label 推 pixmap，
        但 label 此时已经进入销毁队列，访问就会 crash。
        `RuntimeError` 说明信号已经被断过了，吞掉即可。
        """
        if self.camera_worker:
            try:
                self.camera_worker.frame_ready.disconnect(self._on_frame)
            except RuntimeError:
                pass
        super().done(result)


class VirtualKeyboardDialog(QDialog):
    """字母页 / 符号页虚拟键盘。"""

    LETTER_ROWS = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
    SYMBOL_ROWS = [
        list("1234567890"),
        list("!@#$%^&*()"),
        ["-", "_", "=", "+", "[", "]", "{", "}", ";", ":"],
        ["'", '"', ",", ".", "/", "?", "\\", "|", "~", "`"],
    ]

    _KEY_STYLE = """
        QPushButton {
            background-color: #2a2a2e;
            color: #ffffff;
            border: 1px solid #444449;
            border-radius: 6px;
            font-size: 16px;
            font-weight: 600;
        }
        QPushButton:hover { background-color: #3a3a3f; }
        QPushButton:pressed { background-color: #007AFF; }
    """
    _FN_STYLE = """
        QPushButton {
            background-color: #444449;
            color: #ffffff;
            border: 1px solid #555559;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
        }
        QPushButton:hover { background-color: #55555a; }
        QPushButton:pressed { background-color: #007AFF; }
    """

    def __init__(self, initial_text="", title="虚拟键盘", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(
            "QDialog { background-color: #1a1a1e; } QLabel { color: #ffffff; }"
        )
        self._uppercase = True
        self._page = "letters"
        self._letter_buttons = []
        self._key_rows_layout = QVBoxLayout()
        self._key_rows_layout.setSpacing(6)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        self.preview = QLineEdit(initial_text)
        self.preview.setMinimumHeight(44)
        self.preview.setStyleSheet(
            """
            QLineEdit {
                color: #ffffff;
                background-color: #2a2a2e;
                border: 2px solid #007AFF;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 18px;
                font-weight: 700;
                selection-background-color: #007AFF;
            }
            """
        )
        layout.addWidget(self.preview)
        layout.addLayout(self._key_rows_layout)

        fn_row = QHBoxLayout()
        fn_row.setSpacing(6)

        self.page_btn = QPushButton("符号")
        self.page_btn.setFixedHeight(44)
        self.page_btn.setMinimumWidth(72)
        self.page_btn.setStyleSheet(self._FN_STYLE)
        self.page_btn.clicked.connect(self._toggle_page)
        fn_row.addWidget(self.page_btn)

        self.shift_btn = QPushButton("大小写")
        self.shift_btn.setFixedHeight(44)
        self.shift_btn.setMinimumWidth(72)
        self.shift_btn.setStyleSheet(self._FN_STYLE)
        self.shift_btn.clicked.connect(self._toggle_case)
        fn_row.addWidget(self.shift_btn)

        back_btn = QPushButton("退格")
        back_btn.setFixedHeight(44)
        back_btn.setMinimumWidth(72)
        back_btn.setStyleSheet(self._FN_STYLE)
        back_btn.clicked.connect(self._backspace)
        fn_row.addWidget(back_btn)

        clear_btn = QPushButton("清空")
        clear_btn.setFixedHeight(44)
        clear_btn.setMinimumWidth(72)
        clear_btn.setStyleSheet(self._FN_STYLE)
        clear_btn.clicked.connect(lambda: self.preview.setText(""))
        fn_row.addWidget(clear_btn)

        space_btn = QPushButton("空格")
        space_btn.setFixedHeight(44)
        space_btn.setMinimumWidth(120)
        space_btn.setStyleSheet(self._FN_STYLE)
        space_btn.clicked.connect(lambda: self._append_char(" "))
        fn_row.addWidget(space_btn)

        fn_row.addStretch(1)

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(44)
        cancel_btn.setMinimumWidth(90)
        cancel_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #5a5a5f;
                color: #fff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover { background-color: #636368; }
            """
        )
        cancel_btn.clicked.connect(self.reject)
        fn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("确定")
        ok_btn.setFixedHeight(44)
        ok_btn.setMinimumWidth(90)
        ok_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #007AFF;
                color: #fff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover { background-color: #0A84FF; }
            """
        )
        ok_btn.clicked.connect(self.accept)
        fn_row.addWidget(ok_btn)

        layout.addLayout(fn_row)
        self._render_keyboard_page()

    @staticmethod
    def _button_text(ch):
        return "&&" if ch == "&" else ch

    def _clear_key_rows(self):
        while self._key_rows_layout.count():
            item = self._key_rows_layout.takeAt(0)
            child_layout = item.layout()
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                while child_layout.count():
                    child_item = child_layout.takeAt(0)
                    child_widget = child_item.widget()
                    if child_widget is not None:
                        child_widget.deleteLater()
                child_layout.deleteLater()

    def _render_keyboard_page(self):
        self._clear_key_rows()
        self._letter_buttons = []
        if self._page == "letters":
            self._render_letter_page()
        else:
            self._render_symbol_page()
        self.page_btn.setText("符号" if self._page == "letters" else "字母")

    def _render_letter_page(self):
        for idx, row in enumerate(self.LETTER_ROWS):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(6)
            if idx == 1:
                row_layout.addSpacing(22)
            elif idx == 2:
                row_layout.addSpacing(66)
            for ch in row:
                display_ch = ch.upper() if self._uppercase else ch.lower()
                btn = QPushButton(display_ch)
                btn.setFixedSize(44, 44)
                btn.setStyleSheet(self._KEY_STYLE)
                btn.clicked.connect(lambda _=False, c=ch: self._append_char(c))
                row_layout.addWidget(btn)
                self._letter_buttons.append(btn)
            if idx == 1:
                row_layout.addSpacing(22)
            elif idx == 2:
                row_layout.addSpacing(66)
            row_layout.addStretch(1)
            self._key_rows_layout.addLayout(row_layout)

    def _render_symbol_page(self):
        for row in self.SYMBOL_ROWS:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(6)
            for ch in row:
                btn = QPushButton(self._button_text(ch))
                btn.setFixedSize(44, 44)
                btn.setStyleSheet(self._KEY_STYLE)
                btn.clicked.connect(lambda _=False, c=ch: self._append_char(c))
                row_layout.addWidget(btn)
            row_layout.addStretch(1)
            self._key_rows_layout.addLayout(row_layout)

    def _toggle_page(self):
        self._page = "symbols" if self._page == "letters" else "letters"
        self._render_keyboard_page()

    def _append_char(self, ch):
        if self._page == "letters" and ch.isalpha():
            ch = ch.upper() if self._uppercase else ch.lower()
        self.preview.setText(self.preview.text() + ch)

    def _backspace(self):
        text = self.preview.text()
        self.preview.setText(text[:-1])

    def _toggle_case(self):
        self._uppercase = not self._uppercase
        if self._page == "letters":
            for btn in self._letter_buttons:
                txt = btn.text()
                btn.setText(txt.upper() if self._uppercase else txt.lower())

    def get_text(self):
        return self.preview.text()


class AddTrayDialog(QDialog):
    """新增料盘对话框，录入基础参数和首槽坐标。"""

    DEFAULT_JOG_SPEED = 1000

    jog_requested = Signal(str, int, int)
    origin_saved = Signal(dict)

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

    def __init__(
        self,
        existing_ids,
        coordinate_provider=None,
        center_status_provider=None,
        parent=None,
        initial_data=None,
        edit_mode=False,
    ):
        super().__init__(parent)
        self.setWindowTitle("编辑料盘" if edit_mode else "新增料盘")
        self.setFixedSize(920, 700)
        self.setStyleSheet("QDialog { background-color: #1a1a1e; } QLabel { color: #ffffff; }")
        self.existing_ids = existing_ids
        self.coordinate_provider = coordinate_provider
        self.center_status_provider = center_status_provider
        self.initial_data = initial_data or {}
        self.edit_mode = bool(edit_mode)
        self.original_tray_id = str(
            self.initial_data.get("tray_id")
            or self.initial_data.get("id")
            or ""
        ).strip()
        self._numeric_spin_editors = {}
        self._last_center_status = {"ok": False, "message": "等待芯片 ROI 检测..."}

        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(10)
        root_layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("编辑料盘" if self.edit_mode else "新增料盘")
        title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        root_layout.addWidget(title)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(18)
        root_layout.addLayout(content_layout, 1)

        left_panel = QWidget()
        right_panel = QWidget()
        content_layout.addWidget(left_panel, 1)
        content_layout.addWidget(right_panel, 1)

        layout = QVBoxLayout(left_panel)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(10)
        right_layout.setContentsMargins(0, 0, 0, 0)

        id_label = QLabel("料盘编号（唯一）:")
        id_label.setStyleSheet(self._LABEL_STYLE)
        layout.addWidget(id_label)

        id_row = QHBoxLayout()
        id_row.setSpacing(6)

        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("点击弹出键盘，或直接输入料盘编号")
        self.id_input.setMinimumHeight(38)
        self.id_input.setStyleSheet(self._FIELD_STYLE)
        self.id_input.installEventFilter(self)
        self.id_input.setEnabled(True)
        id_row.addWidget(self.id_input, 1)

        keyboard_btn = QPushButton("键盘")
        keyboard_btn.setFixedHeight(38)
        keyboard_btn.setMinimumWidth(64)
        keyboard_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #444449;
                color: #ffffff;
                border: 1px solid #555559;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #55555a; }
            QPushButton:pressed { background-color: #007AFF; }
            """
        )
        keyboard_btn.clicked.connect(self._open_keyboard)
        id_row.addWidget(keyboard_btn)

        layout.addLayout(id_row)

        spec_label = QLabel("料盘规格:")
        spec_label.setStyleSheet(self._LABEL_STYLE)
        layout.addWidget(spec_label)

        self.spec_combo = QComboBox()
        self.spec_combo.setMinimumHeight(38)
        self.spec_combo.setStyleSheet(
            self._FIELD_STYLE
            + """
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #1a1a1e;
                color: #ffffff;
                selection-background-color: #007AFF;
            }
            """
        )
        for preset in TRAY_SPEC_PRESETS:
            self.spec_combo.addItem(preset["label"], preset["key"])
        self.spec_combo.addItem("自定义规格", CUSTOM_TRAY_SPEC_KEY)
        self.spec_combo.currentIndexChanged.connect(self._update_custom_spec_state)
        layout.addWidget(self.spec_combo)

        self.custom_spec_widget = QWidget()
        custom_layout = QHBoxLayout(self.custom_spec_widget)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(8)

        custom_rows_label = QLabel("行(X轴)")
        custom_rows_label.setStyleSheet(self._LABEL_STYLE)
        custom_layout.addWidget(custom_rows_label)

        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 99)
        self.rows_spin.setValue(3)
        self.rows_spin.setMinimumHeight(38)
        self.rows_spin.setStyleSheet(self._FIELD_STYLE)
        self.rows_spin.valueChanged.connect(self._update_custom_spec_summary)
        custom_layout.addWidget(self.rows_spin)

        custom_cols_label = QLabel("列(Y轴)")
        custom_cols_label.setStyleSheet(self._LABEL_STYLE)
        custom_layout.addWidget(custom_cols_label)

        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, 99)
        self.cols_spin.setValue(7)
        self.cols_spin.setMinimumHeight(38)
        self.cols_spin.setStyleSheet(self._FIELD_STYLE)
        self.cols_spin.valueChanged.connect(self._update_custom_spec_summary)
        custom_layout.addWidget(self.cols_spin)

        layout.addWidget(self.custom_spec_widget)

        self.custom_summary = QLabel("")
        self.custom_summary.setStyleSheet("color: #34C759; font-size: 13px;")
        layout.addWidget(self.custom_summary)

        pitch_row = QHBoxLayout()
        pitch_row.setSpacing(8)
        pitch_row.addWidget(self._small_label("行间距"))
        self.pitch_x_spin = self._distance_spin(1.0)
        pitch_row.addWidget(self.pitch_x_spin)
        pitch_row.addWidget(self._small_label("列间距"))
        self.pitch_y_spin = self._distance_spin(1.0)
        pitch_row.addWidget(self.pitch_y_spin)
        layout.addLayout(pitch_row)

        origin_label = QLabel("首个槽位原点坐标（mm）:")
        origin_label.setStyleSheet(self._LABEL_STYLE)
        layout.addWidget(origin_label)

        origin_row = QHBoxLayout()
        origin_row.setSpacing(8)
        origin_row.addWidget(self._small_label("X"))
        self.origin_x_spin = self._coordinate_spin()
        origin_row.addWidget(self.origin_x_spin)
        origin_row.addWidget(self._small_label("Y"))
        self.origin_y_spin = self._coordinate_spin()
        origin_row.addWidget(self.origin_y_spin)
        origin_row.addWidget(self._small_label("Z"))
        self.origin_z_spin = self._coordinate_spin()
        origin_row.addWidget(self.origin_z_spin)
        layout.addLayout(origin_row)
        layout.addStretch()

        layout = right_layout

        motion_label = QLabel("三轴控制（mm）:")
        motion_label.setStyleSheet(self._LABEL_STYLE)
        layout.addWidget(motion_label)

        self.center_status_label = QLabel("芯片居中状态：等待芯片 ROI 检测...")
        self.center_status_label.setWordWrap(True)
        self.center_status_label.setStyleSheet(
            """
            color: #FFD60A;
            background-color: rgba(255, 214, 10, 0.12);
            border: 2px solid rgba(255, 214, 10, 0.75);
            border-radius: 8px;
            padding: 10px;
            font-size: 18px;
            font-weight: 800;
            line-height: 1.4;
            """
        )
        layout.addWidget(self.center_status_label)

        self.axis_step_spins = {}
        for axis in ("X", "Y", "Z"):
            axis_row = QHBoxLayout()
            axis_row.setSpacing(8)
            axis_row.addWidget(self._small_label(f"{axis}步长"))
            step_spin = QDoubleSpinBox()
            step_spin.setRange(0.001, 1000.0)
            step_spin.setDecimals(3)
            step_spin.setValue(pulses_to_mm(axis.lower(), 5000))
            step_spin.setSingleStep(1.0)
            step_spin.setSuffix(" mm")
            step_spin.setMinimumHeight(36)
            step_spin.setStyleSheet(self._double_spin_style())
            self._enable_numeric_keyboard(step_spin, f"输入 {axis} 轴步长")
            self.axis_step_spins[axis.lower()] = step_spin
            axis_row.addWidget(step_spin, 1)

            minus_btn = QPushButton(f"{axis}-")
            minus_btn.setFixedHeight(36)
            minus_btn.setStyleSheet(TemplateConfirmDialog._secondary_button_style())
            minus_btn.clicked.connect(lambda _=False, a=axis.lower(): self._request_jog(a, -1))
            axis_row.addWidget(minus_btn)

            plus_btn = QPushButton(f"{axis}+")
            plus_btn.setFixedHeight(36)
            plus_btn.setStyleSheet(TemplateConfirmDialog._secondary_button_style())
            plus_btn.clicked.connect(lambda _=False, a=axis.lower(): self._request_jog(a, 1))
            axis_row.addWidget(plus_btn)
            layout.addLayout(axis_row)

        collect_row = QHBoxLayout()
        collect_row.setSpacing(8)
        get_coord_btn = QPushButton("获取当前坐标（设为原点）")
        get_coord_btn.setFixedHeight(38)
        get_coord_btn.setStyleSheet(TemplateConfirmDialog._secondary_button_style())
        get_coord_btn.clicked.connect(self._get_current_position)
        collect_row.addWidget(get_coord_btn)
        layout.addLayout(collect_row)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(40)
        cancel_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #5a5a5f;
                color: #fff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover { background-color: #636368; }
            """
        )
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("保存修改" if self.edit_mode else "确认新增")
        ok_btn.setFixedHeight(40)
        ok_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #007AFF;
                color: #fff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
            }
            QPushButton:hover { background-color: #0A84FF; }
            """
        )
        ok_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(ok_btn)

        root_layout.addLayout(btn_layout)

        self._update_custom_spec_state()
        self._apply_initial_data()
        self._center_status_timer = QTimer(self)
        self._center_status_timer.setInterval(300)
        self._center_status_timer.timeout.connect(self._update_center_status)
        self._center_status_timer.start()
        self._update_center_status()

    def eventFilter(self, obj, event):
        """在名称输入框上点击时自动弹软键盘。

        这样物理键盘用户仍能直接敲，触屏用户点一下就能弹键盘。
        只拦截 MouseButtonPress，其它事件交还 Qt 默认处理。
        """
        if obj is self.id_input and event.type() == QEvent.Type.MouseButtonPress:
            self._open_keyboard()
            return True
        if obj in self._numeric_spin_editors and event.type() == QEvent.Type.MouseButtonPress:
            spin, title = self._numeric_spin_editors[obj]
            self._open_numeric_keyboard(spin, title)
            return True
        return super().eventFilter(obj, event)

    def _open_keyboard(self):
        """打开虚拟键盘，用户点确定后把结果写回编号输入框。"""
        dlg = VirtualKeyboardDialog(self.id_input.text(), "输入料盘编号", self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.id_input.setText(dlg.get_text())

    def _enable_numeric_keyboard(self, spin, title):
        editor = spin.lineEdit()
        editor.installEventFilter(self)
        self._numeric_spin_editors[editor] = (spin, title)

    def _open_numeric_keyboard(self, spin, title):
        if isinstance(spin, QDoubleSpinBox):
            initial_text = f"{spin.value():.3f}".rstrip("0").rstrip(".")
        else:
            initial_text = str(int(spin.value()))
        dlg = VirtualKeyboardDialog(initial_text, title, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = dlg.get_text().strip()
        try:
            value = float(text) if isinstance(spin, QDoubleSpinBox) else int(float(text))
        except ValueError:
            QMessageBox.warning(self, "提示", "请输入合法数字。")
            return
        spin.setValue(value)

    def _update_custom_spec_state(self, _index=None):
        """规格切换时同步行列 SpinBox；行列区始终显示，便于现场确认。"""
        spec_key = self.spec_combo.currentData()
        if spec_key != CUSTOM_TRAY_SPEC_KEY:
            for preset in TRAY_SPEC_PRESETS:
                if preset["key"] == spec_key:
                    self.rows_spin.setValue(preset["rows"])
                    self.cols_spin.setValue(preset["cols"])
                    break
        self.custom_spec_widget.setVisible(True)
        self.custom_summary.setVisible(True)
        self._update_custom_spec_summary()

    def _update_custom_spec_summary(self):
        """SpinBox 变化时刷新下方的提示文本（显示 "当前自定义规格: 4x6 (24槽)"）。"""
        rows = self.rows_spin.value()
        cols = self.cols_spin.value()
        self.custom_summary.setText(f"当前自定义规格: {_build_spec_label(rows, cols)}")

    def _on_confirm(self):
        """点击"确认新增"：校验完整参数后 accept，否则弹提示不关窗。"""
        tray_id = self.id_input.text().strip()
        if not tray_id:
            QMessageBox.warning(self, "提示", "请输入料盘编号。")
            return
        if tray_id in self.existing_ids and (not self.edit_mode or tray_id != self.original_tray_id):
            QMessageBox.warning(self, "提示", f"料盘编号 {tray_id} 已存在，请使用其他编号。")
            return
        if self.rows_spin.value() <= 0 or self.cols_spin.value() <= 0:
            QMessageBox.warning(self, "提示", "行数、列数必须大于 0。")
            return
        if self.pitch_x_spin.value() <= 0 or self.pitch_y_spin.value() <= 0:
            QMessageBox.warning(self, "提示", "行间距、列间距必须大于 0。")
            return
        if not self._ensure_chip_centered():
            return
        if not self.edit_mode and not self._capture_current_position_as_origin(show_success=False):
            return
        self.accept()

    def get_tray_id(self):
        """返回用户录入的料盘编号（首尾空格已 strip），同时作为内部唯一键。"""
        return self.id_input.text().strip()

    def get_spec_key(self):
        """返回用户选择的规格键（形如 ``"3x7"``）。

        - 选预设：直接返回预设的 key；
        - 选自定义：把 SpinBox 行列拼成 ``"{rows}x{cols}"``。
        """
        return _build_spec_key(self.rows_spin.value(), self.cols_spin.value())

    def get_tray_data(self):
        """返回完整料盘参数。"""
        return {
            "tray_id": self.get_tray_id(),
            "name": self.get_tray_id(),
            "spec": self.get_spec_key(),
            "rows": self.rows_spin.value(),
            "cols": self.cols_spin.value(),
            "pitch_x": self._pitch_x_pulses(),
            "pitch_y": self._pitch_y_pulses(),
            "pitch_unit": "mm",
            "origin_x": self._coordinate_pulses("x", self.origin_x_spin.value()),
            "origin_y": self._coordinate_pulses("y", self.origin_y_spin.value()),
            "origin_z": self._coordinate_pulses("z", self.origin_z_spin.value()),
        }

    def _small_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("color: #a1a1a6; font-size: 13px;")
        return label

    def _distance_spin(self, value):
        spin = QDoubleSpinBox()
        spin.setRange(0.001, 1000000.0)
        spin.setDecimals(3)
        spin.setSingleStep(0.1)
        spin.setSuffix(" mm")
        spin.setValue(value)
        spin.setMinimumHeight(38)
        spin.setStyleSheet(self._double_spin_style())
        return spin

    def _coordinate_spin(self):
        spin = QDoubleSpinBox()
        spin.setRange(-10000.0, 10000.0)
        spin.setDecimals(3)
        spin.setSingleStep(1.0)
        spin.setSuffix(" mm")
        spin.setMinimumHeight(38)
        spin.setStyleSheet(self._double_spin_style())
        return spin

    def _get_current_position(self):
        if not self._ensure_chip_centered():
            return
        self._capture_current_position_as_origin(show_success=True)

    def _capture_current_position_as_origin(self, show_success=False):
        if self.coordinate_provider is None:
            QMessageBox.warning(self, "提示", "当前未配置坐标读取接口。")
            return False
        position = self.coordinate_provider()
        if not position:
            return False
        self.set_current_position(position)
        self.origin_saved.emit({
            "x": int(position["x"]),
            "y": int(position["y"]),
            "z": int(position["z"]),
        })
        if show_success:
            QMessageBox.information(self, "获取成功", "已获取当前坐标作为原点。")
        return True

    def set_current_position(self, position):
        self.origin_x_spin.setValue(self._pulses_to_mm("x", position["x"]))
        self.origin_y_spin.setValue(self._pulses_to_mm("y", position["y"]))
        self.origin_z_spin.setValue(self._pulses_to_mm("z", position["z"]))

    def _request_jog(self, axis, direction):
        step_mm = float(self.axis_step_spins[axis].value())
        pulses = self._coordinate_pulses(axis, step_mm) * int(direction)
        self.jog_requested.emit(axis, pulses, self.DEFAULT_JOG_SPEED)

    def _update_center_status(self):
        status = self._read_center_status()
        self._last_center_status = status
        if status.get("ok"):
            color = "#34C759"
            prefix = "芯片居中参考：已居中" if self.edit_mode else "芯片居中状态：已居中"
        else:
            color = "#FFD60A"
            prefix = "芯片居中参考：未居中" if self.edit_mode else "芯片居中状态：未居中"
        border_color = "#34C759" if status.get("ok") else "#FFD60A"
        bg_color = "rgba(52, 199, 89, 0.14)" if status.get("ok") else "rgba(255, 214, 10, 0.12)"
        self.center_status_label.setStyleSheet(
            f"""
            color: {color};
            background-color: {bg_color};
            border: 2px solid {border_color};
            border-radius: 8px;
            padding: 10px;
            font-size: 18px;
            font-weight: 800;
            line-height: 1.4;
            """
        )
        self.center_status_label.setText(f"{prefix}\n{status.get('message', '')}")

    def _read_center_status(self):
        if self.center_status_provider is None:
            return {"ok": False, "message": "当前未配置芯片居中检测接口。"}
        try:
            return dict(self.center_status_provider() or {})
        except Exception as exc:
            return {"ok": False, "message": f"芯片居中检测失败：{exc}"}

    def _ensure_chip_centered(self):
        status = self._read_center_status()
        self._last_center_status = status
        self._update_center_status()
        if status.get("ok") or self.edit_mode:
            return True
        QMessageBox.warning(
            self,
            "芯片未居中",
            (
                "请继续移动 X/Y 轴，使第一颗芯片位于摄像头画面中心。\n"
                "摄像头预览十字变为绿色后，才能获取当前坐标并新增料盘。\n\n"
                "提示中的距离按画面偏差估算，实际点动方向以现场运动方向为准。\n\n"
                f"{status.get('message', '')}"
            ),
        )
        return False

    def _update_pitch_unit(self):
        for spin in (self.pitch_x_spin, self.pitch_y_spin):
            spin.setDecimals(3)
            spin.setSingleStep(0.1)
            spin.setSuffix(" mm")

    def _pitch_x_pulses(self):
        return x_mm_to_pulses(self.pitch_x_spin.value())

    def _pitch_y_pulses(self):
        return y_mm_to_pulses(self.pitch_y_spin.value())

    @staticmethod
    def _coordinate_pulses(axis, value_mm):
        axis_key = str(axis).lower()
        if axis_key == "x":
            return x_mm_to_pulses(value_mm)
        if axis_key == "y":
            return y_mm_to_pulses(value_mm)
        if axis_key == "z":
            return z_mm_to_pulses(value_mm)
        raise ValueError(f"非法轴名称：{axis}")

    @staticmethod
    def _pulses_to_mm(axis, value):
        return pulses_to_mm(axis, float(value or 0))

    def _apply_initial_data(self):
        if not self.initial_data:
            return
        tray_name = (
            self.initial_data.get("tray_id")
            or self.initial_data.get("id")
            or self.initial_data.get("name")
        )
        if tray_name:
            self.id_input.setText(str(tray_name))
        rows = int(self.initial_data.get("rows", 3) or 3)
        cols = int(self.initial_data.get("cols", 7) or 7)
        self.rows_spin.setValue(rows)
        self.cols_spin.setValue(cols)
        spec_key = _build_spec_key(rows, cols)
        index = self.spec_combo.findData(spec_key)
        self.spec_combo.setCurrentIndex(index if index >= 0 else self.spec_combo.findData(CUSTOM_TRAY_SPEC_KEY))
        self.pitch_x_spin.setValue(self._pulses_to_mm("x", self.initial_data.get("pitchX") or 10000))
        self.pitch_y_spin.setValue(self._pulses_to_mm("y", self.initial_data.get("pitchY") or 10000))
        origin = self.initial_data.get("firstSlotOrigin") or {}
        self.origin_x_spin.setValue(self._pulses_to_mm("x", origin.get("x") or 0))
        self.origin_y_spin.setValue(self._pulses_to_mm("y", origin.get("y") or 0))
        self.origin_z_spin.setValue(self._pulses_to_mm("z", origin.get("z") or 0))

    @staticmethod
    def _double_spin_style():
        return """
            QDoubleSpinBox {
                color: #ffffff;
                background-color: #2a2a2e;
                border: 1px solid #444449;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: 500;
            }
            QDoubleSpinBox:focus { border: 2px solid #007AFF; }
        """


class SlotMoveConfirmDialog(QDialog):
    """槽位切换确认对话框。

    实时识别模式下，每个槽位识别完成后弹出，显示当前结果并等待工人
    将摄像头移至下一槽位后点击"确认已就位"继续，或点击"停止识别"中止任务。

    按钮语义
    --------
    - accept() → 工人已将摄像头移至下一槽位，继续识别；
    - reject() → 工人主动停止，通知外层中止 ``LiveInspectionWorker``。

    Parameters
    ----------
    current_slot : int
        刚完成识别的槽位（1 基准，用于显示）。
    next_slot : int
        下一个待识别的槽位（1 基准，用于提示工人）。
    current_status : str
        当前槽位的中文识别结果（"正常" / "异常" / "识别失败" 等）。
    current_color : str
        颜色键（"green" / "red"），决定结果文字颜色。
    total_slots : int
        当前料盘总槽位数，用于显示进度。
    parent : QWidget | None
        父控件。
    """

    # 状态颜色映射
    _STATUS_COLORS = {
        "green": "#34C759",
        "red": "#FF3B30",
        "default": "#a1a1a6",
    }

    def __init__(self, current_slot, next_slot, current_status,
                 current_color, total_slots, parent=None):
        super().__init__(parent)
        self.setWindowTitle("槽位确认")
        self.setFixedSize(520, 320)
        self.setStyleSheet("QDialog { background-color: #1a1a1e; } QLabel { color: #ffffff; }")

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(28, 28, 28, 28)

        # 标题：进度提示
        title = QLabel(f"槽位 {current_slot} / {total_slots} 识别完成")
        title.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # 识别结果显示
        color_hex = self._STATUS_COLORS.get(current_color, self._STATUS_COLORS["default"])
        result_label = QLabel(f"识别结果：{current_status}")
        result_label.setStyleSheet(
            f"color: {color_hex}; font-size: 22px; font-weight: 700;"
        )
        result_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(result_label)

        # 操作提示
        tip = QLabel(f'请将摄像头移至槽位 {next_slot}，就位后点击“确认已就位”。')
        tip.setStyleSheet("color: #FFD60A; font-size: 15px;")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setWordWrap(True)
        layout.addWidget(tip)

        layout.addStretch()

        # 操作按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(14)

        stop_btn = QPushButton("停止实时识别")
        stop_btn.setFixedHeight(50)
        stop_btn.setStyleSheet(
            """
            QPushButton {
                background-color: rgba(255, 59, 48, 0.18);
                color: #FF3B30;
                border: 1px solid rgba(255, 59, 48, 0.6);
                border-radius: 8px;
                font-size: 15px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: rgba(255, 59, 48, 0.30); }
            QPushButton:pressed { background-color: rgba(255, 59, 48, 0.45); }
            """
        )
        stop_btn.clicked.connect(self.reject)
        btn_layout.addWidget(stop_btn)

        confirm_btn = QPushButton("确认已就位")
        confirm_btn.setFixedHeight(50)
        confirm_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #34C759;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-size: 17px;
                font-weight: 700;
            }
            QPushButton:hover { background-color: #31DC74; }
            QPushButton:pressed { background-color: #2BA84B; }
            """
        )
        confirm_btn.setDefault(True)
        confirm_btn.clicked.connect(self.accept)
        btn_layout.addWidget(confirm_btn)

        layout.addLayout(btn_layout)

