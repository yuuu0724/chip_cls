"""实时逐槽位检测线程。

工作流程（每个槽位）
--------------------
1. 若不是第一个槽位，发出 ``request_move_confirm`` 信号，等待 UI 自动移槽完成；
2. 移动到位后静止等待 ``capture_settle_ms``，再从摄像头缓存抓取一帧；
3. 所有槽位拍照完成后，在后台统一逐张调用 OCR 引擎推理；
4. 将识别结果通过 ``slot_recognized`` 信号回到主线程更新 UI，
   UI 可在自动模式下先执行运动控制，再调用 ``confirm_move()``。

模式
----
``mode`` 保留为日志字段；当前 UI 统一按自动移槽流程调用 ``confirm_move()``。

线程安全
--------
主线程通过 Qt 信号触发 ``confirm_move()``（在主线程执行），用 ``threading.Event``
解除工作线程的 ``wait()`` 阻塞，无共享可变状态，不需要额外锁。
"""

import logging
import threading

from PySide6.QtCore import QThread, Signal

from ocr import MaterialController

logger = logging.getLogger(__name__)

# 旧的三帧一致参数保留为兼容入参；新流程每槽位只拍一张图，统一后台识别
DEFAULT_MIN_RETRY_ROUNDS = 3
DEFAULT_MAX_RETRY_ROUNDS = 6
DEFAULT_CAPTURE_SETTLE_MS = 800


class LiveInspectionWorker(QThread):
    """实时逐槽位检测线程。

    Signals
    -------
    slot_recognized : (int, str, str)
        单个槽位识别完毕后发射，参数为
        ``(slot_index, 中文状态, 颜色键)``，UI 层连 ``update_slot_ui`` 即可。
    request_move_confirm : (int)
        请求主线程弹出"移至下一槽位"确认框，参数为 **下一个** slot_index（0基准）。
    all_done : ()
        全部槽位识别完毕后发射。
    status_message : (str)
        进度提示文字，供 UI 日志区或状态栏显示。
    """

    slot_recognized = Signal(int, str, str)
    request_move_confirm = Signal(int)
    all_done = Signal()
    status_message = Signal(str)

    def __init__(
        self,
        engine,
        camera_worker,
        target_m,
        target_a,
        data_logger,
        total_slots,
        slot_order=None,
        min_retry_rounds=DEFAULT_MIN_RETRY_ROUNDS,
        max_retry_rounds=DEFAULT_MAX_RETRY_ROUNDS,
        capture_settle_ms=DEFAULT_CAPTURE_SETTLE_MS,
        mode="auto",
        parent=None,
    ):
        """
        Parameters
        ----------
        engine : OCREngine
            OCR 推理引擎（与批量检测共享同一实例，类级 session 不重复加载）。
        camera_worker : CameraWorker
            正在运行的摄像头预览线程；从其 ``current_frame_bgr`` 属性读取最新帧。
        target_m : str
            目标型号。
        target_a : str | int
            目标角度。
        data_logger : DataLogger
            CSV 日志记录器。
        total_slots : int
            当前料盘总槽位数。
        slot_order : list[int] | None
            实际执行的 0 基准槽位索引顺序；为空时按自然顺序执行。
        mode : str
            日志字段，当前由 UI 统一使用 ``"auto"``。
        """
        super().__init__(parent)
        self.engine = engine
        self.camera_worker = camera_worker
        self.target_m = target_m
        self.target_a = target_a
        self.data_logger = data_logger
        self.total_slots = total_slots
        self.slot_order = self._normalize_slot_order(slot_order, total_slots)
        self.min_retry_rounds, self.max_retry_rounds = self._normalize_retry_rounds(
            min_retry_rounds,
            max_retry_rounds,
        )
        self.capture_settle_ms = self._normalize_capture_settle_ms(capture_settle_ms)
        self.display_slot_numbers = {
            slot_index: order_pos + 1
            for order_pos, slot_index in enumerate(self.slot_order)
        }
        self.mode = mode

        # 停止标志；外部调用 stop() 后置 True
        self._stop_flag = False
        self._pause_requested = threading.Event()
        self.was_stopped = False
        # 槽位移动确认事件；工人点"确认"或 UI 自动移槽完成后 set()
        self._move_confirmed = threading.Event()

    @staticmethod
    def _normalize_slot_order(slot_order, total_slots):
        if slot_order is None:
            return list(range(total_slots))
        normalized = []
        seen = set()
        for slot_index in slot_order:
            try:
                index = int(slot_index)
            except (TypeError, ValueError):
                continue
            if 0 <= index < total_slots and index not in seen:
                normalized.append(index)
                seen.add(index)
        return normalized or list(range(total_slots))

    @staticmethod
    def _normalize_retry_rounds(min_rounds, max_rounds):
        try:
            min_rounds = int(min_rounds)
        except (TypeError, ValueError):
            min_rounds = DEFAULT_MIN_RETRY_ROUNDS
        try:
            max_rounds = int(max_rounds)
        except (TypeError, ValueError):
            max_rounds = DEFAULT_MAX_RETRY_ROUNDS

        min_rounds = max(1, min_rounds)
        max_rounds = max(min_rounds, max_rounds)
        return min_rounds, max_rounds

    @staticmethod
    def _normalize_capture_settle_ms(value):
        try:
            settle_ms = int(value)
        except (TypeError, ValueError):
            settle_ms = DEFAULT_CAPTURE_SETTLE_MS
        return max(0, settle_ms)

    def _display_slot_number(self, slot_index):
        return self.display_slot_numbers.get(slot_index, slot_index + 1)

    # ------------------------------------------------------------------
    # 外部控制接口
    # ------------------------------------------------------------------

    def stop(self):
        """外部请求停止识别。

        同时 set() 确认事件，让正在 wait() 的工作线程能干净退出，
        不至于永远阻塞。
        """
        self._stop_flag = True
        self.was_stopped = True
        self._pause_requested.clear()
        self._move_confirmed.set()

    def pause(self):
        """暂停后续采集和识别。"""
        if not self._stop_flag:
            self._pause_requested.set()

    def resume(self):
        """继续执行暂停中的实时识别。"""
        self._pause_requested.clear()

    def confirm_move(self):
        """主线程回调：工人已确认摄像头移到位，解除 wait() 阻塞继续识别。

        必须在主线程调用（由主线程的信号槽或按钮事件触发）。
        """
        self._move_confirmed.set()

    def _wait_if_paused(self):
        """暂停时短轮询等待，确保 stop() 能快速唤醒线程退出。"""
        while self._pause_requested.is_set() and not self._stop_flag:
            self.status_message.emit("检测已暂停，等待继续...")
            self.msleep(100)
        return not self._stop_flag

    # ------------------------------------------------------------------
    # 内部推理逻辑
    # ------------------------------------------------------------------

    @staticmethod
    def _format_texts_with_scores(result):
        parts = []
        for item in result.get("all_items") or result.get("items", []) or []:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            try:
                score = float(item.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            parts.append(f"{text}({score:.2%})")
        if parts:
            return "|".join(parts)

        texts = result.get("all_texts") or result.get("texts", [])
        return "|".join(str(text) for text in texts)

    def _sleep_interruptibly(self, duration_ms, pause_message=None):
        """可被暂停和停止打断的毫秒级等待。"""
        waited_ms = 0
        duration_ms = max(0, int(duration_ms))
        while waited_ms < duration_ms and not self._stop_flag:
            if pause_message:
                self.status_message.emit(pause_message)
            if not self._wait_if_paused():
                return False
            step_ms = min(100, duration_ms - waited_ms)
            self.msleep(step_ms)
            waited_ms += step_ms
        return not self._stop_flag

    def _capture_slot_frame(self, slot_index):
        """移动到位并静止后，从 CameraWorker 读取一张槽位照片。"""
        display_slot_no = self._display_slot_number(slot_index)
        if self.capture_settle_ms > 0:
            self.status_message.emit(
                f"槽位 {display_slot_no} 已到位，静止 {self.capture_settle_ms} ms 后拍照..."
            )
            if not self._sleep_interruptibly(self.capture_settle_ms):
                return None

        frame = getattr(self.camera_worker, "current_frame_bgr", None)
        if frame is None:
            logger.warning("槽位 %02d 拍照失败：摄像头帧为空", display_slot_no)
            return None
        logger.info("槽位 %02d 已拍照 frame_shape=%s", display_slot_no, frame.shape)
        return frame.copy()

    def _infer_captured_frame(self, slot_index, source_frame):
        """对已缓存照片执行 OCR 推理和业务判定。

        Returns
        -------
        tuple[str, str, dict]
            ``(status, color_key, result_dict)``。
        """
        if source_frame is None:
            return "识别失败", "red", {
                "texts": [],
                "all_texts": [],
                "all_items": [],
                "items": [],
                "angle": 0,
                "status": "error: no frame",
                "reason": "拍照时摄像头帧为空",
            }

        result = self.engine.predict_image_from_array(
            source_frame.copy(),
            target_angle=self.target_a,
        )
        raw_status = str(result.get("status", ""))
        if raw_status.startswith("error"):
            status, color = "识别失败", "red"
        else:
            status, color = MaterialController.analyze_status(result, self.target_m, self.target_a)
        return status, color, result

    # ------------------------------------------------------------------
    # 线程主体
    # ------------------------------------------------------------------

    def run(self):
        """线程主体：逐槽位移动拍照 → 全部照片统一推理 → 发信号。"""
        logger.info(
            "========== 实时检测开始，共 %d 个槽位，模式=%s，顺序=%s，静止等待=%dms ==========",
            len(self.slot_order),
            self.mode,
            [self._display_slot_number(index) for index in self.slot_order],
            self.capture_settle_ms,
        )

        captured_frames = []
        for order_pos, slot_index in enumerate(self.slot_order):
            if self._stop_flag or not self._wait_if_paused():
                break

            # 非首个槽位：等待 UI 自动移动到新槽位并确认；首槽位由 UI 启动前移动到位。
            if order_pos > 0:
                self._move_confirmed.clear()
                self.request_move_confirm.emit(slot_index)
                # UI 自动移槽完成后调用 confirm_move()
                while not self._move_confirmed.wait(0.1):
                    if self._stop_flag:
                        break

                if self._stop_flag or not self._wait_if_paused():
                    break

            display_slot_no = self._display_slot_number(slot_index)
            self.status_message.emit(f"正在拍摄槽位 {display_slot_no}...")
            captured_frames.append((slot_index, self._capture_slot_frame(slot_index)))

        if not self._stop_flag:
            logger.info("========== 拍照完成，开始后台统一识别，共 %d 张 ==========", len(captured_frames))

        for slot_index, source_frame in captured_frames:
            if self._stop_flag or not self._wait_if_paused():
                break

            display_slot_no = self._display_slot_number(slot_index)
            self.status_message.emit(f"正在后台识别槽位 {display_slot_no}...")
            status, color, result = self._infer_captured_frame(slot_index, source_frame)

            if self._stop_flag:
                break

            # 写 CSV 日志（1 基准编号）
            texts = result.get("texts", [])
            angle = result.get("angle", 0)
            self.data_logger.log_result(
                display_slot_no,
                self._format_texts_with_scores(result),
                angle,
                status,
            )
            self.data_logger.save_slot_image(
                display_slot_no,
                source_frame,
                selected_chip_bbox=result.get("selected_chip_bbox"),
            )

            # 通知 UI 更新槽位显示
            self.slot_recognized.emit(slot_index, status, color)
            logger.info(
                "槽位 %02d => %s (texts=%s, angle=%s)",
                display_slot_no, status, texts, angle,
            )

        logger.info("========== 实时检测完成 ==========")
        self.all_done.emit()
