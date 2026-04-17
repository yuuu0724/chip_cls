"""数据日志记录模块"""
import csv
import os
from datetime import datetime


class DataLogger:
    def __init__(self, base_dir="results"):
        self.base_dir = base_dir
        self.sheet_dir = os.path.join(self.base_dir, "sheet")
        self.image_dir = os.path.join(self.base_dir, "images")

        self.current_file = None
        self.current_image_file = None
        self.current_tray = None
        self.batch_count = 0

        self.expected_slots = 21
        self._record_count = 0
        self._screenshot_saved = False

        # 创建结果目录结构：results/sheet + results/images
        os.makedirs(self.sheet_dir, exist_ok=True)
        os.makedirs(self.image_dir, exist_ok=True)

    def start_new_batch(self, tray_id="A0001"):
        """
        开始新一轮检测，创建新的结果文件
        返回当前文件路径
        """
        self.current_tray = tray_id
        self.batch_count += 1
        self._record_count = 0
        self._screenshot_saved = False

        # 生成文件名：tray_id_batch批次_时间戳
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{tray_id}_batch{self.batch_count}_{timestamp}"
        self.current_file = os.path.join(self.sheet_dir, f"{base_name}.csv")
        self.current_image_file = os.path.join(self.image_dir, f"{base_name}.jpg")

        # 初始化表头
        try:
            with open(self.current_file, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["时间", "料位", "所有识别文本", "识别角度", "检测结果"])
        except Exception as e:
            print(f"创建结果文件失败: {e}")

        return self.current_file

    def log_result(self, slot_id, all_text, angle, status):
        """记录单次识别的详细数据"""
        if not self.current_file:
            print("警告：未初始化结果文件，跳过记录")
            return

        # 记录精确到秒
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_msg = str(all_text).replace("\n", " ").replace("\r", "")

        try:
            with open(self.current_file, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([now, slot_id, clean_msg, angle, status])
        except Exception as e:
            print(f"写入CSV失败: {e}")
            return

        self._record_count += 1

        # 每次整盘识别完毕后，自动保存当前Qt界面截图（同名jpg）
        if not self._screenshot_saved and self._is_batch_finished(slot_id):
            self._save_ui_screenshot()

    def _is_batch_finished(self, slot_id):
        """判断当前是否完成整盘识别。"""
        try:
            return int(slot_id) >= self.expected_slots
        except (TypeError, ValueError):
            return self._record_count >= self.expected_slots

    def _save_ui_screenshot(self):
        """抓取整个Qt主界面并保存为jpg。"""
        if not self.current_image_file:
            return

        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtWidgets import QApplication
        except Exception as e:
            print(f"截图失败：未能导入Qt截图依赖: {e}")
            return

        app = QApplication.instance()
        if app is None:
            print("截图失败：未检测到QApplication实例")
            return

        # 优先抓主活动窗口，回退到可见顶层窗口
        window = app.activeWindow()
        if window is None:
            for widget in app.topLevelWidgets():
                if widget.isVisible():
                    window = widget
                    break

        if window is None:
            print("截图失败：未找到可见窗口")
            return

        pixmap = window.grab()
        if pixmap.isNull():
            screen = window.screen() or QGuiApplication.primaryScreen()
            if screen is None:
                print("截图失败：未找到屏幕对象")
                return
            geo = window.frameGeometry()
            pixmap = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())

        if pixmap.isNull():
            print("截图失败：抓取到的图像为空")
            return

        # 控制图片大小，避免文件过大
        max_width = 1920
        max_height = 1080
        if pixmap.width() > max_width or pixmap.height() > max_height:
            pixmap = pixmap.scaled(
                max_width,
                max_height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        quality = 75
        if not pixmap.save(self.current_image_file, "JPG", quality):
            print(f"截图失败：无法保存图片到 {self.current_image_file}")
            return

        self._screenshot_saved = True