"""RS232 通信接口（预留，暂未实现）。

后续与 PLC / 外部设备打通时，在此模块填充具体的串口通信逻辑。
目前所有方法均为空占位符，业务代码可直接调用而不会报错。

使用方式（未来）
--------------
::

    rs232 = RS232Interface()
    rs232.bind_worker(live_worker)   # 绑定实时识别线程
    rs232.open("/dev/ttyUSB0", 9600) # 打开串口

    # 识别线程内部会自动调用 on_slot_recognized；
    # 串口收到"到位"帧时调用 on_slot_move_done，驱动 worker 继续识别。
"""

import logging

logger = logging.getLogger(__name__)


class RS232Interface:
    """RS232 通信预留接口。

    手动 / 自动模式下均由 ``LiveInspectionWorker`` 在适当时机调用，
    主线程无需感知通信细节。
    """

    def __init__(self):
        # 预留：实现时持有串口对象和绑定的 worker 引用
        self._worker = None

    def bind_worker(self, worker):
        """绑定实时识别线程，以便 on_slot_move_done 能触发 worker.confirm_move()。

        Parameters
        ----------
        worker : LiveInspectionWorker
            当前正在运行的实时识别线程。
        """
        self._worker = worker

    def on_slot_recognized(self, slot_id, result):
        """每个槽位识别完成后调用，向外部设备发送"移至下一槽位"指令。

        Parameters
        ----------
        slot_id : int
            1 基准的槽位编号（与 CSV 日志对齐）。
        result : dict
            识别结果，至少含 ``{"status": str, "color": str}``。

        Notes
        -----
        预留接口：此处应通过串口发送指令帧，帧格式待硬件协议确定后填充。
        """
        # TODO: 实现串口发送逻辑，示例：
        #   frame = build_move_command(slot_id, result["status"])
        #   self._serial.write(frame)
        logger.debug("[RS232 预留] on_slot_recognized slot_id=%d result=%s", slot_id, result)

    def on_slot_move_done(self, slot_id):
        """接收外部设备"已到位"指令后回调，触发下一槽位识别。

        Parameters
        ----------
        slot_id : int
            1 基准的槽位编号，表示摄像头已移至该槽位上方。

        Notes
        -----
        预留接口：实现时需解析串口收到的帧，提取槽位编号后调用
        ``self._worker.confirm_move()`` 驱动识别线程继续。
        """
        # TODO: 从帧中解析 slot_id，校验后再 confirm_move
        logger.debug("[RS232 预留] on_slot_move_done slot_id=%d", slot_id)
        if self._worker is not None:
            self._worker.confirm_move()
