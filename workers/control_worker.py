"""背景检测线程模块"""
import logging
import os
import time

import cv2
from PySide6.QtCore import QThread, Signal
from ocr import MaterialController

logger = logging.getLogger(__name__)


class ControlWorker(QThread):
    """后台逻辑线程：负责识别与数据记录（串行推理，逐槽更新 UI）"""
    progress_update = Signal(int, str, str)  # 槽位索引, 状态文字, 颜色键
    finished = Signal()

    def __init__(self, engine, img_dir, target_m, target_a, data_logger, total_slots=21, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.img_dir = img_dir
        self.target_m = target_m
        self.target_a = target_a
        self.data_logger = data_logger
        self.total_slots = total_slots

    def _preload_images(self, files):
        """预加载所有图片到内存，将 I/O 与推理解耦。"""
        images = {}
        for f in files:
            path = os.path.join(self.img_dir, f)
            img = cv2.imread(path)
            if img is not None:
                images[f] = img
                logger.debug("预加载图片: %s shape=%s", f, img.shape)
            else:
                logger.warning("无法读取图片: %s", path)
        return images

    def _emit_result(self, slot_index, res):
        """处理单个槽位的结果：写日志并发射 UI 更新信号。"""
        res_texts = res.get("texts", [])
        angle = res.get("angle", 0)

        if str(res.get("status", "")).startswith("error"):
            status, color = "识别失败", "red"
        elif not res_texts and res.get("status") == "empty":
            status, color = "空", "yellow"
        else:
            status, color = MaterialController.analyze_status(res, self.target_m, self.target_a)

        raw_status = res.get("status", "")
        logger.info("槽位 %02d => %s (texts=%s, angle=%s, status=%s)", slot_index + 1, status, res_texts, angle, raw_status)
        self.data_logger.log_result(slot_index + 1, "|".join(res_texts), angle, status)
        self.progress_update.emit(slot_index, status, color)

    def run(self):
        batch_start = time.time()
        logger.info("========== 开始检测 ==========")
        logger.info("目标型号=%s  目标角度=%s  图像目录=%s", self.target_m, self.target_a, self.img_dir)

        # 排序确保 1.png 对应 01 槽位
        files = [f for f in os.listdir(self.img_dir) if f.lower().endswith(('.png', '.jpg'))]
        files.sort(key=lambda x: int(''.join(filter(str.isdigit, x)) or 0))
        logger.info("找到 %d 张图片: %s", len(files), files)

        # 预加载全部图片
        t0 = time.time()
        preloaded = self._preload_images(files)
        logger.info("预加载完成: %d 张, 耗时 %.2fs", len(preloaded), time.time() - t0)

        # 串行推理，逐槽更新 UI
        for i in range(self.total_slots):
            if i < len(files) and files[i] in preloaded:
                t1 = time.time()
                res = self.engine.predict_image_from_array(preloaded[files[i]])
                elapsed = time.time() - t1
                logger.info("槽位 %02d 推理完成, 耗时 %.3fs", i + 1, elapsed)
            else:
                res = {"texts": [], "angle": 0, "status": "empty"}

            self._emit_result(i, res)

        total = time.time() - batch_start
        logger.info("========== 检测完成, 总耗时 %.2fs ==========", total)
        self.finished.emit()
