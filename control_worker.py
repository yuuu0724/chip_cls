"""
背景检测线程模块
"""
import os
from PySide6.QtCore import QThread, Signal
from logic_controller import MaterialController


class ControlWorker(QThread):
    """后台逻辑线程：负责识别与数据记录"""
    progress_update = Signal(int, str, str)  # 槽位索引, 状态文字, 颜色键
    finished = Signal()

    def __init__(self, engine, img_dir, target_m, target_a, logger, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.img_dir = img_dir
        self.target_m = target_m
        self.target_a = target_a
        self.logger = logger

    def run(self):
        # 排序确保 1.png 对应 01 槽位
        files = [f for f in os.listdir(self.img_dir) if f.lower().endswith(('.png', '.jpg'))]
        files.sort(key=lambda x: int(''.join(filter(str.isdigit, x)) or 0))

        for i in range(21):
            # 初始化本轮状态，防止上一轮数据干扰
            status, color = "空", "yellow"
            res_texts = []
            angle = 0

            if i < len(files):
                res = self.engine.predict_image(os.path.join(self.img_dir, files[i]))
                res_texts = res.get("texts", [])
                angle = res.get("angle", 0)
                # 调用逻辑判定
                status, color = MaterialController.analyze_status(res, self.target_m, self.target_a)

            # 记录到 CSV：使用管道符分隔，清晰明了
            self.logger.log_result(i + 1, "|".join(res_texts), angle, status)
            
            # 发送信号
            self.progress_update.emit(i, status, color)
        
        self.finished.emit()
