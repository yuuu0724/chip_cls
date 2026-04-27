"""数据日志记录模块。

每轮批量检测会：
1. 生成一个新的 CSV 文件（``results/sheet/*.csv``），按槽位顺序追加行；
2. 批次完成后由主线程保存一张界面截图（``results/images/*.jpg``）。

CSV 列顺序：``时间 | 料位 | 所有识别文本 | 识别角度 | 检测结果``。
"""

import csv
import os
from datetime import datetime


class DataLogger:
    """单批检测的数据日志器。

    Parameters
    ----------
    base_dir : str
        输出根目录，默认 ``results``。子目录 ``sheet/`` 存 CSV，
        ``images/`` 存界面截图。
    """

    def __init__(self, base_dir="results"):
        self.base_dir = base_dir
        self.sheet_dir = os.path.join(self.base_dir, "sheet")
        self.image_dir = os.path.join(self.base_dir, "images")

        # 当前批次的状态；start_new_batch 会重置
        self.current_file = None          # 当前 CSV 绝对路径
        self.current_image_file = None    # 当前批次结束时要保存的截图路径
        self.current_tray = None          # 当前料盘 ID
        self.batch_count = 0              # 程序启动以来跑过的批次数

        self.expected_slots = 21          # 当前批次应有的槽位数（用于 is_batch_finished）
        self._record_count = 0            # 已写入的 CSV 行数
        self._screenshot_saved = False    # 避免重复保存截图

        os.makedirs(self.sheet_dir, exist_ok=True)
        os.makedirs(self.image_dir, exist_ok=True)

    def start_new_batch(self, tray_id="A0001", expected_slots=None):
        """开始新一轮检测：重置状态、创建新的 CSV 文件。

        Parameters
        ----------
        tray_id : str
            当前料盘 ID，会写进文件名便于回溯。
        expected_slots : int | None
            本批次预计有多少个槽位；决定 `is_batch_finished` 何时返回 True。
            不传则沿用上次的值（首次为 21）。

        Returns
        -------
        str | None
            新建的 CSV 绝对路径；创建失败会打印错误但仍返回路径。
        """
        self.current_tray = tray_id or "UNKNOWN"
        self.batch_count += 1
        self._record_count = 0
        self._screenshot_saved = False

        if expected_slots is not None:
            try:
                self.expected_slots = max(1, int(expected_slots))
            except (TypeError, ValueError):
                self.expected_slots = 21

        # 文件名：<料盘ID>_batch<第N批>_<时间戳>
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{self.current_tray}_batch{self.batch_count}_{timestamp}"
        self.current_file = os.path.join(self.sheet_dir, f"{base_name}.csv")
        self.current_image_file = os.path.join(self.image_dir, f"{base_name}.jpg")

        # 建文件并写表头（utf-8-sig 让 Excel 直接识别中文不乱码）
        try:
            with open(self.current_file, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["时间", "料位", "所有识别文本", "识别角度", "检测结果"])
        except Exception as e:
            print(f"创建结果文件失败: {e}")

        return self.current_file

    def log_result(self, slot_id, all_text, angle, status):
        """追加一行单槽位检测结果到 CSV。

        Parameters
        ----------
        slot_id : int
            槽位编号（1 基准，UI 显示和 CSV 对齐）。
        all_text : str
            本槽位识别到的全部文本；多条文本预先用 ``"|"`` 连接。
        angle : int
            识别到的方向角度。
        status : str
            中文状态（"正常" / "方向错误" 等）。

        写入失败不抛异常，只打印警告 —— 不让日志故障阻断整批检测。
        """
        if not self.current_file:
            print("警告：未初始化结果文件，跳过记录")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # CSV 单元格内禁止换行，否则 Excel 会把一行拆成多行
        clean_msg = str(all_text).replace("\n", " ").replace("\r", "")

        try:
            with open(self.current_file, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([now, slot_id, clean_msg, angle, status])
        except Exception as e:
            print(f"写入 CSV 失败: {e}")
            return

        self._record_count += 1

    def is_batch_finished(self, slot_id=None):
        """判断当前批次是否已完成。

        Parameters
        ----------
        slot_id : int | None
            如果传入，判断该槽位编号是否已经到达 expected_slots；
            不传则看累计记录数。
        """
        if slot_id is not None:
            try:
                return int(slot_id) >= self.expected_slots
            except (TypeError, ValueError):
                pass
        return self._record_count >= self.expected_slots

    def save_ui_screenshot(self, window):
        """保存主界面截图。

        ⚠️ 重要：必须在 Qt 主线程调用，不能从 `ControlWorker` 里直接触发。
        历史上 log_result 里直接调用过 `window.grab()`，在最后一格识别完
        后出现过 UI 闪退/卡死——原因是跨线程拿 pixmap 不安全。

        Parameters
        ----------
        window : QMainWindow | None
            主窗口实例；会先尝试 `grab()`，失败再退化到
            `screen.grabWindow()`。

        Returns
        -------
        bool
            True 表示成功保存。下列情况返回 False：
            - 已经保存过一次（幂等）
            - 当前没有打开的批次文件
            - 批次尚未完成
            - 未提供窗口或抓图为空
            - 写磁盘失败
        """
        if self._screenshot_saved or not self.current_image_file:
            return False

        if not self.is_batch_finished():
            return False

        if window is None:
            print("截图失败：未提供可见窗口")
            return False

        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QGuiApplication
        except Exception as e:
            print(f"截图失败：未能导入 Qt 截图依赖: {e}")
            return False

        # 首选：直接 grab widget，快且不依赖屏幕坐标
        pixmap = window.grab()
        if pixmap.isNull():
            # 兜底：按窗口几何从屏幕抓图（某些驱动上 grab() 可能返回空 pixmap）
            screen = window.screen() or QGuiApplication.primaryScreen()
            if screen is None:
                print("截图失败：未找到屏幕对象")
                return False
            geo = window.frameGeometry()
            pixmap = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())

        if pixmap.isNull():
            print("截图失败：抓取到的图像为空")
            return False

        # 超过 1080p 就等比缩小到 1920x1080，控制单张截图体积
        max_width = 1920
        max_height = 1080
        if pixmap.width() > max_width or pixmap.height() > max_height:
            pixmap = pixmap.scaled(
                max_width,
                max_height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        # JPG 质量 75 —— 肉眼看不出损失，体积比 PNG 小一个数量级
        if not pixmap.save(self.current_image_file, "JPG", 75):
            print(f"截图失败：无法保存图片到 {self.current_image_file}")
            return False

        self._screenshot_saved = True
        return True
