"""
摄像头预览线程模块
"""
import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QPixmap, QImage


class CameraWorker(QThread):
    """摄像头预览线程"""
    frame_ready = Signal(QPixmap)
    
    def __init__(self, camera_id=1):
        super().__init__()
        self.camera_id = camera_id
        self.is_running = True
        self.cap = None
    
    def run(self):
        self.cap = cv2.VideoCapture(self.camera_id)
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
            
            cv2.waitKey(30)
    
    def stop(self):
        self.is_running = False
        if self.cap:
            self.cap.release()
