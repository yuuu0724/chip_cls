"""摄像头预览线程模块。

使用 OpenCV 抓帧，通过 Qt 信号把 `QPixmap` 扔回主线程供 UI 显示。
放在独立 `QThread` 里是为了避免阻塞 Qt 事件循环（一帧抓取 + 颜色转换
往往会有 ~30ms 开销，主线程直接跑会导致界面卡顿）。
"""

import cv2
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage, QPixmap


class CameraWorker(QThread):
    """摄像头预览线程。

    生命周期
    --------
    1. 主线程 `start()` 启动线程后进入 `run()`；
    2. `run()` 里循环 `cap.read()` 抓帧，每帧通过 `frame_ready` 信号发回主线程；
    3. 外部调用 `stop()` 把 `is_running` 置 False 并释放摄像头资源；
    4. 主线程 `wait()` 直到线程退出后即可安全销毁对象。

    Signals
    -------
    frame_ready : QPixmap
        每抓取一帧就发射一次，UI 层 connect 后更新 label。
    """

    frame_ready = Signal(QPixmap)

    def __init__(self, camera_id=0):
        """构造线程对象；此时还未真正打开摄像头。

        Parameters
        ----------
        camera_id : int
            摄像头索引；0 通常是内置相机，1/2 是外接 USB 相机。
        """
        super().__init__()
        self.camera_id = camera_id
        self.is_running = True
        self.cap = None  # VideoCapture，在 run() 里真正打开
        # 最新一帧的原始 BGR numpy array（未水平翻转），供实时识别线程读取
        self.current_frame_bgr = None

    def _open_camera(self):
        """尝试多种后端打开摄像头，返回 None 表示全部失败。

        Windows 上 OpenCV 默认后端（MSMF/V4L2）在部分 USB 相机上不稳定，
        先尝试 DirectShow（CAP_DSHOW）通常更可靠，退化到自动后端 CAP_ANY。
        """
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
        """线程主循环：打开摄像头 -> 循环抓帧 -> 发信号。

        抓帧失败（`ret is False`）时不会 break，会继续循环 —— 这样一时的
        USB 抖动不至于让预览彻底停掉。`is_running` 置 False 才会退出。
        """
        self.cap = self._open_camera()
        if self.cap is None:
            return

        # 预览不需要高分辨率，320x240 足够看清，同时也节省 CPU
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

        while self.is_running:
            ret, frame = self.cap.read()
            if ret:
                # 保存原始 BGR 帧（翻转前），供实时识别线程直接读取做 OCR
                self.current_frame_bgr = frame.copy()
                # 水平翻转让画面符合"照镜子"直觉
                frame = cv2.flip(frame, 1)
                # OpenCV 默认 BGR，Qt 要求 RGB
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_frame.shape
                bytes_per_line = ch * w
                qt_image = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qt_image)
                self.frame_ready.emit(pixmap)
            # 约 33FPS，足够流畅同时不占满 CPU
            self.msleep(30)

    def stop(self):
        """外部调用：请求线程退出并释放摄像头。

        调用后还需要 `wait()` 确保 `run()` 真的返回，才能安全销毁对象。
        """
        self.is_running = False
        if self.cap:
            self.cap.release()
