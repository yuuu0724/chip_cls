"""摄像头预览线程模块"""
import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QPixmap, QImage


class CameraWorker(QThread):
    """摄像头预览线程"""
    frame_ready = Signal(QPixmap)
    
    def __init__(self, camera_id=0):
        super().__init__()
        self.camera_id = camera_id
        self.is_running = True
        self.cap = None

    def _open_camera(self):
        backends = []
        if hasattr(cv2, "CAP_DSHOW"):
            backends.append(cv2.CAP_DSHOW)
        backends.append(cv2.CAP_ANY)

        for backend in backends:
            cap = cv2.VideoCapture(self.camera_id, backend)
            if cap is not None and cap.isOpened():
                return cap
            if cap is not None:
                cap.release()
        return None
    
    def run(self):
        self.cap = self._open_camera()
        if self.cap is None:
            return

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        
        while self.is_running:
            ret, frame = self.cap.read()
            if ret:
                # 翻转和转换颜色
                frame = cv2.flip(frame, 1)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_frame.shape
                bytes_per_line = ch * w
                qt_image = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qt_image)
                self.frame_ready.emit(pixmap)
            self.msleep(30)
    
    def stop(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
