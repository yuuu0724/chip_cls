"""后台批量检测线程。

按槽位逐格跑 OCR，并把每格结果通过 Qt 信号回送主线程更新 UI。
之所以独立一个 QThread，是为了避免 OCR 推理（单次 0.x~数秒）把 UI
事件循环阻塞导致界面冻结。
"""

import logging
import os
import time

import cv2
from PySide6.QtCore import QThread, Signal

from ocr import MaterialController

logger = logging.getLogger(__name__)


class ControlWorker(QThread):
    """批量检测线程：串行识别、记录结果并逐格更新 UI。

    Signals
    -------
    progress_update : (int, str, str)
        单个槽位完成后发射，参数依次是
        ``(slot_index, 中文状态, 颜色键)``，UI 层连 `update_slot_ui` 即可。
    finished : ()
        全部槽位处理完后发射，UI 层可以据此重置按钮状态 + 保存截图。
    """

    progress_update = Signal(int, str, str)
    finished = Signal()

    def __init__(self, engine, img_dir, target_m, target_a, data_logger, total_slots=21, parent=None):
        """
        Parameters
        ----------
        engine : OCREngine
            OCR 推理引擎；由 AppServices 提供，线程内复用主线程构造好的实例。
        img_dir : str
            图像所在目录。目录下的 .png/.jpg 文件按文件名中的数字排序后
            依次对应槽位 1, 2, ...。
        target_m : str
            目标型号（用于 `MaterialController.analyze_status` 比对）。
        target_a : str | int
            目标角度。
        data_logger : DataLogger
            日志记录器，每格识别完都会写一行 CSV。
        total_slots : int
            当前料盘应有的槽位总数（来自 UI 层根据料盘规格计算）。
        """
        super().__init__(parent)
        self.engine = engine
        self.img_dir = img_dir
        self.target_m = target_m
        self.target_a = target_a
        self.data_logger = data_logger
        self.total_slots = total_slots

    def _preload_images(self, files):
        """把所有图片一次性读到内存。

        把磁盘 I/O 一次批量做完，推理循环里只做 CPU/GPU 计算，让 I/O 与推理
        解耦，整体耗时更低（磁盘在冷启动时 I/O 尤其慢）。

        Parameters
        ----------
        files : list[str]
            图片文件名列表（不含目录）。

        Returns
        -------
        dict[str, numpy.ndarray]
            文件名到 BGR 图像数组的映射。读取失败的文件会被跳过。
        """
        images = {}
        for file_name in files:
            path = os.path.join(self.img_dir, file_name)
            img = cv2.imread(path)
            if img is not None:
                images[file_name] = img
                logger.debug("预加载图片 %s shape=%s", file_name, img.shape)
            else:
                logger.warning("无法读取图片: %s", path)
        return images

    def _emit_result(self, slot_index, result):
        """把单格 OCR 原始结果 -> 业务状态 -> 写日志 + 发信号。

        Parameters
        ----------
        slot_index : int
            0 基准的槽位索引；写日志/UI 显示时会 +1。
        result : dict
            引擎返回或 `missing` fallback 产生的原始结果字典。
        """
        texts = result.get("texts", [])
        angle = result.get("angle", 0)
        raw_status = str(result.get("status", ""))

        # 引擎自身报错 -> 直接识别失败；否则交给 MaterialController 判定四态
        if raw_status.startswith("error"):
            status, color = "识别失败", "red"
        else:
            status, color = MaterialController.analyze_status(result, self.target_m, self.target_a)

        logger.info(
            "槽位 %02d => %s (texts=%s, angle=%s, status=%s)",
            slot_index + 1,
            status,
            texts,
            angle,
            raw_status,
        )
        # 日志用 1 基准编号；"|" 是多文本的轻量分隔符（CSV 字段内不会与逗号冲突）
        self.data_logger.log_result(slot_index + 1, "|".join(texts), angle, status)
        self.progress_update.emit(slot_index, status, color)

    def run(self):
        """线程入口：预加载 -> 逐格推理 -> 发送 finished。

        若某个槽位对应的图片不存在，走 ``missing`` fallback 让 UI 显示
        "识别失败"（红），方便用户一眼看到漏拍/漏放的位置。
        """
        batch_start = time.time()
        logger.info("========== 开始检测 ==========")
        logger.info("目标型号=%s  目标角度=%s  图像目录=%s",
                    self.target_m, self.target_a, self.img_dir)

        # 过滤 + 按文件名中的数字排序（例如 "1.jpg", "2.jpg", ..., "21.jpg"）
        files = [f for f in os.listdir(self.img_dir) if f.lower().endswith((".png", ".jpg"))]
        files.sort(key=lambda name: int("".join(filter(str.isdigit, name)) or 0))
        logger.info("找到 %d 张图片: %s", len(files), files)

        preload_start = time.time()
        preloaded = self._preload_images(files)
        logger.info("预加载完成 %d 张, 耗时 %.2fs",
                    len(preloaded), time.time() - preload_start)

        for index in range(self.total_slots):
            if index < len(files) and files[index] in preloaded:
                infer_start = time.time()
                result = self.engine.predict_image_from_array(preloaded[files[index]])
                logger.info("槽位 %02d 推理完成, 耗时 %.3fs",
                            index + 1, time.time() - infer_start)
            else:
                logger.warning("槽位 %02d 未找到对应图片，按识别失败处理", index + 1)
                result = {"texts": [], "angle": 0, "status": "missing"}

            self._emit_result(index, result)

        logger.info("========== 检测完成, 总耗时 %.2fs ==========",
                    time.time() - batch_start)
        self.finished.emit()
