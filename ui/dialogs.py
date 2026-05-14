"""对话框组件模块。

集中管理 UI 层所有次级弹窗：

- :class:`TemplateConfirmDialog` —— OCR 识别出参考芯片型号后，让用户确认/修改。
- :class:`CameraCaptureDialog` —— 实时预览摄像头并"咔嚓"抓一帧当参考图。
- :class:`VirtualKeyboardDialog` —— 触屏场景下没有物理键盘时的数字+字母软键盘。
- :class:`AddTrayDialog` —— 新增料盘时录入编号和规格。

料盘规格在这里有两个来源：
``TRAY_SPEC_PRESETS`` 提供三种常见预设，用户也可以选"自定义规格"
通过行数/列数自由组合。``CUSTOM_TRAY_SPEC_KEY`` 是 QComboBox 中用于
区分"选了预设"还是"选了自定义"的哨兵键。
"""

import os
import tempfile

from PySide6.QtCore import QEvent, Qt, QThread, QTimer, Signal
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
        current_light_config=None,
        light_adjuster=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("确认模板参数")
        self.setFixedSize(560, 520)
        self.setStyleSheet(
            """
            QDialog { background-color: #1a1a1e; }
            QLabel { color: #ffffff; }
            """
        )
        self._light_config = dict(current_light_config or {})
        self._light_adjuster = light_adjuster
        self.detected_texts = list(detected_texts or [])

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("确认模板参数")
        title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        tips = QLabel("请选择当前模板对应的标准芯片型号；识别为空或错误时可手动输入。")
        tips.setStyleSheet("color: #FFD60A; font-size: 13px; line-height: 1.5;")
        tips.setWordWrap(True)
        layout.addWidget(tips)

        raw_text_label = QLabel("OCR 识别结果（按识别顺序）:")
        raw_text_label.setStyleSheet("color: #a1a1a6; font-size: 13px; margin-top: 6px;")
        layout.addWidget(raw_text_label)

        self.raw_text_display = QTextEdit()
        self.raw_text_display.setPlainText("\n".join(self.detected_texts) or "未识别到文本")
        self.raw_text_display.setReadOnly(True)
        self.raw_text_display.setMaximumHeight(86)
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
        self.model_combo.setEditable(True)
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
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #1a1a1e;
                color: #ffffff;
                selection-background-color: #007AFF;
            }
            """
        )
        seen = set()
        for text in self.detected_texts + list(existing_models or []):
            text = str(text).strip()
            if text and text not in seen:
                self.model_combo.addItem(text)
                seen.add(text)
        if detected_model and detected_model not in seen:
            self.model_combo.insertItem(0, detected_model)
        self.model_combo.setCurrentText(detected_model or "")
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

        light_row = QHBoxLayout()
        light_row.setSpacing(8)
        self.light_label = QLabel(self._format_light_config())
        self.light_label.setStyleSheet("color: #a1a1a6; font-size: 13px;")
        light_row.addWidget(self.light_label, 1)

        light_btn = QPushButton("调光")
        light_btn.setFixedHeight(36)
        light_btn.setStyleSheet(self._secondary_button_style())
        light_btn.clicked.connect(self._open_light_adjustment)
        light_btn.setEnabled(self._light_adjuster is not None)
        light_row.addWidget(light_btn)
        layout.addLayout(light_row)

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

    def get_light_config(self):
        """返回模板绑定的光源配置。"""
        return dict(self._light_config)

    def _open_light_adjustment(self):
        if self._light_adjuster is None:
            return
        config = self._light_adjuster(self._light_config)
        if config:
            self._light_config = dict(config)
            self.light_label.setText(self._format_light_config())

    def _format_light_config(self):
        return (
            "光源配置："
            f"1路 {float(self._light_config.get('light1Voltage', 0.0)):.2f} V，"
            f"2路 {float(self._light_config.get('light2Voltage', 0.0)):.2f} V"
        )

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

    def _on_frame(self, pixmap):
        """CameraWorker 的 `frame_ready` 槽。

        保留原始 pixmap 供拍摄时用（保持最大分辨率），
        展示时再按 preview_label 的大小做保形缩放。
        """
        self._last_pixmap = pixmap
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def _capture(self):
        """按下"拍摄"按钮：把当前最后一帧原图落盘到临时路径并关闭对话框。"""
        if not self._last_pixmap:
            return
        self.captured_path = os.path.join(tempfile.gettempdir(), "ocr_ref_capture.png")
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


class LightOcrWorker(QThread):
    """调光面板专用 OCR 线程，避免识别阻塞 UI。"""

    result_ready = Signal(object, object)

    def __init__(self, engine, frame_bgr, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.frame_bgr = frame_bgr

    def run(self):
        try:
            result = self.engine.predict_image_from_array(self.frame_bgr)
        except Exception as exc:
            result = {"angle": -1, "texts": [], "items": [], "status": f"error: {exc}"}
        self.result_ready.emit(self.frame_bgr, result)


class LightAdjustDialog(QDialog):
    """两路光源调光对话框，复用主窗口摄像头预览。"""

    def __init__(self, camera_worker, device_controller, engine=None, initial_config=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("光源调节")
        self.setFixedSize(720, 640)
        self.setStyleSheet(
            """
            QDialog { background-color: #1a1a1e; }
            QLabel { color: #ffffff; }
            """
        )
        self.camera_worker = camera_worker
        self.device_controller = device_controller
        self.engine = engine
        self.light_config = dict(initial_config or {})
        self._last_frame_bgr = None
        self._last_ocr_result = None
        self._ocr_worker = None

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_lights)
        self._ocr_timer = QTimer(self)
        self._ocr_timer.setInterval(1200)
        self._ocr_timer.timeout.connect(self._start_ocr_preview)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("光源调节")
        title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        tip = QLabel("请调整光源，使芯片上的所有字符清晰可见且识别正确。")
        tip.setStyleSheet("color: #FFD60A; font-size: 14px;")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.preview_label = QLabel("等待摄像头信号...")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(680, 420)
        self.preview_label.setScaledContents(False)
        self.preview_label.setStyleSheet(
            "background-color: #0a0e1a; border: 1px solid rgba(0,122,255,0.3); border-radius: 8px;"
        )
        layout.addWidget(self.preview_label, 1)

        self.ocr_status_label = QLabel("识别结果：等待画面")
        self.ocr_status_label.setStyleSheet("color: #34C759; font-size: 13px;")
        self.ocr_status_label.setWordWrap(True)
        layout.addWidget(self.ocr_status_label)

        light1_row = QHBoxLayout()
        light1_row.setSpacing(8)
        light1_row.addWidget(self._label("1 路光源电压"))
        self.light1_spin = self._voltage_spin(self.light_config.get("light1Voltage", 0.0))
        light1_row.addWidget(self.light1_spin)
        layout.addLayout(light1_row)

        light2_row = QHBoxLayout()
        light2_row.setSpacing(8)
        light2_row.addWidget(self._label("2 路光源电压"))
        self.light2_spin = self._voltage_spin(self.light_config.get("light2Voltage", 0.0))
        light2_row.addWidget(self.light2_spin)
        layout.addLayout(light2_row)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        apply_btn = QPushButton("应用当前电压")
        apply_btn.setFixedHeight(42)
        apply_btn.setStyleSheet(self._secondary_button_style())
        apply_btn.clicked.connect(self._apply_lights)
        btn_layout.addWidget(apply_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(42)
        cancel_btn.setStyleSheet(TemplateConfirmDialog._cancel_button_style())
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("确认保存")
        ok_btn.setFixedHeight(42)
        ok_btn.setStyleSheet(TemplateConfirmDialog._ok_button_style())
        ok_btn.clicked.connect(self._on_accept)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

        if self.camera_worker:
            self.camera_worker.frame_ready.connect(self._on_frame)
        if self.engine is not None:
            self._ocr_timer.start()

    def _label(self, text):
        label = QLabel(text)
        label.setStyleSheet("color: #a1a1a6; font-size: 14px; min-width: 110px;")
        return label

    def _voltage_spin(self, value):
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 24.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setSuffix(" V")
        spin.setValue(float(value or 0.0))
        spin.setMinimumHeight(38)
        spin.setStyleSheet(
            """
            QDoubleSpinBox {
                color: #ffffff;
                background-color: #2a2a2e;
                border: 1px solid #444449;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 14px;
                font-weight: 600;
            }
            QDoubleSpinBox:focus { border: 2px solid #007AFF; }
            """
        )
        spin.valueChanged.connect(lambda _value: self._apply_timer.start(250))
        return spin

    def _on_frame(self, pixmap):
        frame = getattr(self.camera_worker, "current_frame_bgr", None)
        if frame is None:
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
            return
        self._last_frame_bgr = frame.copy()
        self._render_preview(self._last_frame_bgr, self._last_ocr_result)

    def _start_ocr_preview(self):
        if self.engine is None or self._last_frame_bgr is None:
            return
        if self._ocr_worker is not None and self._ocr_worker.isRunning():
            return
        self._ocr_worker = LightOcrWorker(self.engine, self._last_frame_bgr.copy())
        self._ocr_worker.result_ready.connect(self._on_ocr_result)
        self._ocr_worker.finished.connect(self._ocr_worker.deleteLater)
        self._ocr_worker.finished.connect(self._clear_ocr_worker)
        self._ocr_worker.start()

    def _on_ocr_result(self, frame_bgr, result):
        self._last_ocr_result = result
        texts = result.get("texts", [])
        status = result.get("status", "")
        if texts:
            self.ocr_status_label.setText("识别结果：" + " | ".join(texts))
        else:
            self.ocr_status_label.setText(f"识别结果：{status or '未识别到文本'}")
        self._render_preview(frame_bgr, result)

    def _clear_ocr_worker(self):
        self._ocr_worker = None

    def _render_preview(self, frame_bgr, result=None):
        display_frame = self._build_annotated_frame(frame_bgr, result)
        pixmap = self._bgr_to_pixmap(display_frame)
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def _build_annotated_frame(self, frame_bgr, result):
        try:
            import cv2
            import numpy as np
        except Exception:
            return frame_bgr

        display_frame = frame_bgr.copy()

        if not result:
            return display_frame

        angle = int(result.get("angle", 0) or 0)
        frame_h, frame_w = frame_bgr.shape[:2]
        for item in result.get("items", []):
            box = item.get("box")
            text = str(item.get("text", "")).strip()
            score = item.get("score", 0.0)
            if not box or not text:
                continue

            pts = self._map_ocr_box_to_preview(
                np.array(box, dtype=np.float32),
                angle,
                frame_w,
                frame_h,
            ).astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(display_frame, [pts], True, (0, 255, 0), 2)
            x = max(int(pts[:, 0, 0].min()), 0)
            y = max(int(pts[:, 0, 1].min()) - 8, 18)
            label = f"{text} {float(score):.2f}"
            (tw, th), baseline = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1
            )
            cv2.rectangle(
                display_frame,
                (x, y - th - baseline - 4),
                (x + tw + 6, y + baseline),
                (0, 0, 0),
                -1,
            )
            cv2.putText(
                display_frame,
                label,
                (x + 3, y - 3),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )
        return display_frame

    @staticmethod
    def _map_ocr_box_to_preview(box, angle, frame_w, frame_h):
        """把 OCR 内部转正后的框坐标映射回未旋转的摄像头预览。"""
        mapped = box.copy()
        if angle == 90:
            x_u = box[:, 0].copy()
            y_u = box[:, 1].copy()
            mapped[:, 0] = frame_w - 1 - y_u
            mapped[:, 1] = x_u
        if angle == 180:
            mapped[:, 0] = frame_w - 1 - box[:, 0]
            mapped[:, 1] = frame_h - 1 - box[:, 1]
        if angle == 270:
            x_u = box[:, 0].copy()
            y_u = box[:, 1].copy()
            mapped[:, 0] = y_u
            mapped[:, 1] = frame_h - 1 - x_u
        mapped[:, 0] = mapped[:, 0].clip(0, frame_w - 1)
        mapped[:, 1] = mapped[:, 1].clip(0, frame_h - 1)
        return mapped

    @staticmethod
    def _bgr_to_pixmap(frame_bgr):
        try:
            import cv2
        except Exception:
            return QPixmap()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        image = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(image)

    def _current_config(self):
        return {
            "light1Voltage": float(self.light1_spin.value()),
            "light2Voltage": float(self.light2_spin.value()),
        }

    def _apply_lights(self):
        config = self._current_config()
        if self.device_controller is None:
            self.light_config = config
            return True

        for channel, key in ((1, "light1Voltage"), (2, "light2Voltage")):
            result = self.device_controller.set_light_voltage(channel, config[key])
            if not result.success:
                QMessageBox.warning(self, "调光失败", result.message)
                return False
        self.light_config = config
        return True

    def _on_accept(self):
        if self._apply_lights():
            self.accept()

    def get_light_config(self):
        return dict(self.light_config)

    def done(self, result):
        self._ocr_timer.stop()
        if self.camera_worker:
            try:
                self.camera_worker.frame_ready.disconnect(self._on_frame)
            except RuntimeError:
                pass
        if self._ocr_worker is not None and self._ocr_worker.isRunning():
            try:
                self._ocr_worker.result_ready.disconnect(self._on_ocr_result)
            except RuntimeError:
                pass
        super().done(result)

    @staticmethod
    def _secondary_button_style():
        return """
            QPushButton {
                background-color: #007AFF;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #0A84FF; }
        """


class VirtualKeyboardDialog(QDialog):
    """数字 + 英文字母虚拟键盘（便于触屏输入料盘编号）。

    现场工控屏有时没有物理键盘，也不方便贴屏幕键盘。这里提供一个
    "只含字母数字 + 大小写切换 + 退格/清空/取消/确定" 的极简软键盘：
    布局贴近 QWERTY 手感，按键都是 44x44，符合触摸最小可点击区。

    用法
    ----
    ::

        dlg = VirtualKeyboardDialog("A001", "输入料盘编号", self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            text = dlg.get_text()

    Parameters
    ----------
    initial_text : str
        预填到顶部预览框的内容，一般取控件已有文本。
    title : str
        对话框窗口标题。
    parent : QWidget | None
        父控件。
    """

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
        self._letter_buttons = []

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

        rows = ["1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
        for idx, row in enumerate(rows):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(6)
            if idx == 2:
                row_layout.addSpacing(22)
            elif idx == 3:
                row_layout.addSpacing(66)
            for ch in row:
                btn = QPushButton(ch)
                btn.setFixedSize(44, 44)
                btn.setStyleSheet(self._KEY_STYLE)
                btn.clicked.connect(lambda _=False, c=ch: self._append_char(c))
                row_layout.addWidget(btn)
                if idx >= 1:
                    self._letter_buttons.append(btn)
            if idx == 2:
                row_layout.addSpacing(22)
            elif idx == 3:
                row_layout.addSpacing(66)
            row_layout.addStretch(1)
            layout.addLayout(row_layout)

        fn_row = QHBoxLayout()
        fn_row.setSpacing(6)

        shift_btn = QPushButton("大小写")
        shift_btn.setFixedHeight(44)
        shift_btn.setMinimumWidth(72)
        shift_btn.setStyleSheet(self._FN_STYLE)
        shift_btn.clicked.connect(self._toggle_case)
        fn_row.addWidget(shift_btn)

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

    def _append_char(self, ch):
        """按下字母/数字键：按当前大小写状态追加到预览框末尾。"""
        if ch.isalpha():
            ch = ch.upper() if self._uppercase else ch.lower()
        self.preview.setText(self.preview.text() + ch)

    def _backspace(self):
        """删除预览框末尾一个字符（空串也安全）。"""
        text = self.preview.text()
        self.preview.setText(text[:-1])

    def _toggle_case(self):
        """切换大小写：翻转 `_uppercase` 并同步所有字母键面文字。"""
        self._uppercase = not self._uppercase
        for btn in self._letter_buttons:
            txt = btn.text()
            btn.setText(txt.upper() if self._uppercase else txt.lower())

    def get_text(self):
        """返回用户最终输入的文本（需 exec() 返回 Accepted 才有意义）。"""
        return self.preview.text()


class AddTrayDialog(QDialog):
    """新增料盘对话框，录入基础参数和首槽坐标。"""

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

    def __init__(self, existing_ids, coordinate_provider=None, light_adjuster=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新增料盘")
        self.setFixedSize(600, 740)
        self.setStyleSheet("QDialog { background-color: #1a1a1e; } QLabel { color: #ffffff; }")
        self.existing_ids = existing_ids
        self.coordinate_provider = coordinate_provider
        self.light_adjuster = light_adjuster
        self.light_config = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("新增料盘")
        title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        name_label = QLabel("料盘名称或型号:")
        name_label.setStyleSheet(self._LABEL_STYLE)
        layout.addWidget(name_label)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如: LQFP 标准料盘")
        self.name_input.setMinimumHeight(38)
        self.name_input.setStyleSheet(self._FIELD_STYLE)
        layout.addWidget(self.name_input)

        id_label = QLabel("料盘编号（唯一）:")
        id_label.setStyleSheet(self._LABEL_STYLE)
        layout.addWidget(id_label)

        id_row = QHBoxLayout()
        id_row.setSpacing(6)

        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("点击弹出键盘，或直接输入 例如: A0004")
        self.id_input.setMinimumHeight(38)
        self.id_input.setStyleSheet(self._FIELD_STYLE)
        self.id_input.installEventFilter(self)
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

        custom_rows_label = QLabel("行")
        custom_rows_label.setStyleSheet(self._LABEL_STYLE)
        custom_layout.addWidget(custom_rows_label)

        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 99)
        self.rows_spin.setValue(3)
        self.rows_spin.setMinimumHeight(38)
        self.rows_spin.setStyleSheet(self._FIELD_STYLE)
        self.rows_spin.valueChanged.connect(self._update_custom_spec_summary)
        custom_layout.addWidget(self.rows_spin)

        custom_cols_label = QLabel("列")
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
        pitch_row.addWidget(self._small_label("横向间距"))
        self.pitch_x_spin = self._distance_spin(1.0)
        pitch_row.addWidget(self.pitch_x_spin)
        pitch_row.addWidget(self._small_label("纵向间距"))
        self.pitch_y_spin = self._distance_spin(1.0)
        pitch_row.addWidget(self.pitch_y_spin)
        layout.addLayout(pitch_row)

        origin_label = QLabel("首个槽位原点坐标:")
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

        collect_row = QHBoxLayout()
        collect_row.setSpacing(8)
        get_coord_btn = QPushButton("获取当前坐标")
        get_coord_btn.setFixedHeight(38)
        get_coord_btn.setStyleSheet(TemplateConfirmDialog._secondary_button_style())
        get_coord_btn.clicked.connect(self._get_current_position)
        collect_row.addWidget(get_coord_btn)

        light_btn = QPushButton("调光")
        light_btn.setFixedHeight(38)
        light_btn.setStyleSheet(TemplateConfirmDialog._secondary_button_style())
        light_btn.clicked.connect(self._open_light_adjustment)
        light_btn.setEnabled(self.light_adjuster is not None)
        collect_row.addWidget(light_btn)
        layout.addLayout(collect_row)

        self.light_summary = QLabel("光源配置：未设置")
        self.light_summary.setStyleSheet("color: #a1a1a6; font-size: 13px;")
        layout.addWidget(self.light_summary)

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

        ok_btn = QPushButton("确认新增")
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

        layout.addLayout(btn_layout)

        self._update_custom_spec_state()

    def eventFilter(self, obj, event):
        """在编号输入框上点击时自动弹软键盘。

        这样物理键盘用户仍能直接敲，触屏用户点一下就能弹键盘。
        只拦截 MouseButtonPress，其它事件交还 Qt 默认处理。
        """
        if obj is self.id_input and event.type() == QEvent.Type.MouseButtonPress:
            self._open_keyboard()
            return True
        return super().eventFilter(obj, event)

    def _open_keyboard(self):
        """打开虚拟键盘，用户点确定后把结果写回编号输入框。"""
        dlg = VirtualKeyboardDialog(self.id_input.text(), "输入料盘编号", self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.id_input.setText(dlg.get_text())

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
        if tray_id in self.existing_ids:
            QMessageBox.warning(self, "提示", f"编号 {tray_id} 已存在，请使用其他编号。")
            return
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "提示", "请输入料盘名称或型号。")
            return
        if self.rows_spin.value() <= 0 or self.cols_spin.value() <= 0:
            QMessageBox.warning(self, "提示", "行数、列数必须大于 0。")
            return
        if self.pitch_x_spin.value() <= 0 or self.pitch_y_spin.value() <= 0:
            QMessageBox.warning(self, "提示", "横向间距、纵向间距必须大于 0。")
            return
        self.accept()

    def get_tray_id(self):
        """返回用户录入的料盘编号（首尾空格已 strip）。"""
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
            "name": self.name_input.text().strip(),
            "spec": self.get_spec_key(),
            "rows": self.rows_spin.value(),
            "cols": self.cols_spin.value(),
            "pitch_x": self.pitch_x_spin.value(),
            "pitch_y": self.pitch_y_spin.value(),
            "origin_x": self.origin_x_spin.value(),
            "origin_y": self.origin_y_spin.value(),
            "origin_z": self.origin_z_spin.value(),
            "light_config": dict(self.light_config),
        }

    def _small_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("color: #a1a1a6; font-size: 13px;")
        return label

    def _distance_spin(self, value):
        spin = QDoubleSpinBox()
        spin.setRange(0.001, 10000.0)
        spin.setDecimals(3)
        spin.setSingleStep(0.1)
        spin.setSuffix(" mm")
        spin.setValue(value)
        spin.setMinimumHeight(38)
        spin.setStyleSheet(self._double_spin_style())
        return spin

    def _coordinate_spin(self):
        spin = QDoubleSpinBox()
        spin.setRange(-100000.0, 100000.0)
        spin.setDecimals(3)
        spin.setSingleStep(0.1)
        spin.setSuffix(" mm")
        spin.setMinimumHeight(38)
        spin.setStyleSheet(self._double_spin_style())
        return spin

    def _get_current_position(self):
        if self.coordinate_provider is None:
            QMessageBox.warning(self, "提示", "当前未配置坐标读取接口。")
            return
        position = self.coordinate_provider()
        if not position:
            return
        self.origin_x_spin.setValue(float(position["x"]))
        self.origin_y_spin.setValue(float(position["y"]))
        self.origin_z_spin.setValue(float(position["z"]))

    def _open_light_adjustment(self):
        if self.light_adjuster is None:
            return
        config = self.light_adjuster(self.light_config)
        if config:
            self.light_config = dict(config)
            self.light_summary.setText(
                "光源配置："
                f"1路 {float(self.light_config.get('light1Voltage', 0.0)):.2f} V，"
                f"2路 {float(self.light_config.get('light2Voltage', 0.0)):.2f} V"
            )

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
        当前槽位的中文识别结果（"正常" / "方向错误" 等）。
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
