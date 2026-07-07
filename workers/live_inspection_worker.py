"""实时逐槽位识别线程。

工作流程（每个槽位）
--------------------
1. 若不是第一个槽位，发出 ``request_move_confirm`` 信号，等待 UI 自动移槽完成；
2. 在约 1 秒内连续采集 3 帧摄像头图像，逐帧调用 OCR 引擎推理；
3. 三帧识别结果（status）完全一致 → 视为该槽位识别成功；否则重新采集，
   最多重试 ``_MAX_RETRY_ROUNDS`` 轮；
4. 将最终结果通过 ``slot_recognized`` 信号回到主线程更新 UI，
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

# 每个槽位最多采集多少轮（每轮3帧），超过则强制推进
_MAX_RETRY_ROUNDS = 10
# 每帧间隔毫秒数；3帧合计约 1 秒
_FRAME_INTERVAL_MS = 333


class LiveInspectionWorker(QThread):
    """实时逐槽位识别线程。

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
        self.mode = mode

        # 停止标志；外部调用 stop() 后置 True
        self._stop_flag = False
        self._pause_requested = threading.Event()
        self.was_stopped = False
        # 槽位移动确认事件；工人点"确认"或 UI 自动移槽完成后 set()
        self._move_confirmed = threading.Event()

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
        for item in result.get("items", []) or []:
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

    def _capture_and_infer(self):
        """从 CameraWorker 读最新一帧并推理。

        Returns
        -------
        tuple[str, str, dict]
            ``(status, color_key, result_dict)``。
            若摄像头帧为空，直接返回识别失败。
        """
        frame = getattr(self.camera_worker, "current_frame_bgr", None)
        if frame is None:
            logger.warning("摄像头帧为空，无法推理")
            return "识别失败", "red", {"texts": [], "angle": 0, "status": "error: no frame"}

        result = self.engine.predict_image_from_array(
            frame,
            target_angle=self.target_a,
        )
        raw_status = str(result.get("status", ""))

        if raw_status.startswith("error"):
            status, color = "识别失败", "red"
        else:
            status, color = MaterialController.analyze_status(result, self.target_m, self.target_a)

        return status, color, result

    def _infer_slot_with_consensus(self, slot_index):
        """连续采集 3 帧推理，三帧结果一致则返回，否则重试。

        最多重试 ``_MAX_RETRY_ROUNDS`` 轮（每轮 ~1 秒）；超出后强制取最后一帧结果，
        防止单槽位无限阻塞。

        Returns
        -------
        tuple[str, str, dict]
            ``(status, color_key, result_dict)``。
        """
        last_results = []

        for attempt in range(_MAX_RETRY_ROUNDS):
            if self._stop_flag or not self._wait_if_paused():
                break

            round_results = []
            for frame_idx in range(3):
                if self._stop_flag or not self._wait_if_paused():
                    break
                self.status_message.emit(
                    f"槽位 {slot_index + 1} — 第 {attempt + 1} 轮第 {frame_idx + 1} 帧采集中..."
                )
                status, color, result = self._capture_and_infer()
                round_results.append((status, color, result))
                # 帧间间隔，合计约 1 秒
                waited_ms = 0
                while waited_ms < _FRAME_INTERVAL_MS and not self._stop_flag:
                    if not self._wait_if_paused():
                        break
                    step_ms = min(100, _FRAME_INTERVAL_MS - waited_ms)
                    self.msleep(step_ms)
                    waited_ms += step_ms

            if len(round_results) < 3:
                # 被 stop 打断，不再重试
                last_results = round_results
                break

            last_results = round_results
            # 三帧 status 全一致才算稳定
            statuses = [r[0] for r in round_results]
            if len(set(statuses)) == 1:
                logger.info(
                    "槽位 %02d 连续3帧一致: %s（第 %d 轮）",
                    slot_index + 1, statuses[0], attempt + 1,
                )
                return round_results[0]

            logger.info(
                "槽位 %02d 第 %d 轮结果不一致: %s，重新采集",
                slot_index + 1, attempt + 1, statuses,
            )
            self.status_message.emit(
                f"槽位 {slot_index + 1} 三帧不一致（{statuses}），重新采集..."
            )

        # 超出最大重试次数，取最后一轮第一帧兜底
        if last_results:
            logger.warning("槽位 %02d 超出最大重试轮次，使用最后一帧结果", slot_index + 1)
            return last_results[0]

        return "识别失败", "red", {"texts": [], "angle": 0, "status": "error: max_retry"}

    # ------------------------------------------------------------------
    # 线程主体
    # ------------------------------------------------------------------

    def run(self):
        """线程主体：逐槽位 等确认 → 采集 → 推理 → 发信号。"""
        logger.info("========== 实时识别开始，共 %d 个槽位，模式=%s ==========",
                    self.total_slots, self.mode)

        for slot_index in range(self.total_slots):
            if self._stop_flag or not self._wait_if_paused():
                break

            # 非首个槽位：等待 UI 自动移动到新槽位并确认
            if slot_index > 0:
                self._move_confirmed.clear()
                self.request_move_confirm.emit(slot_index)
                # UI 自动移槽完成后调用 confirm_move()
                while not self._move_confirmed.wait(0.1):
                    if self._stop_flag:
                        break

                if self._stop_flag or not self._wait_if_paused():
                    break

            self.status_message.emit(f"正在识别槽位 {slot_index + 1}...")
            status, color, result = self._infer_slot_with_consensus(slot_index)

            if self._stop_flag:
                break

            # 写 CSV 日志（1 基准编号）
            texts = result.get("texts", [])
            angle = result.get("angle", 0)
            self.data_logger.log_result(
                slot_index + 1,
                self._format_texts_with_scores(result),
                angle,
                status,
            )

            # 通知 UI 更新槽位显示
            self.slot_recognized.emit(slot_index, status, color)
            logger.info(
                "槽位 %02d => %s (texts=%s, angle=%s)",
                slot_index + 1, status, texts, angle,
            )

        logger.info("========== 实时识别完成 ==========")
        self.all_done.emit()
