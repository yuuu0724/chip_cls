"""RS485 通信接口（预留）。

后续与 PLC / 下位机打通时，在此模块填充具体的 485 串口通信逻辑。
目前所有方法均为空占位符，业务代码可直接调用而不会报错。

使用方式（未来）
--------------
::

    rs485 = RS485Interface()
    rs485.bind_worker(live_worker)   # 绑定实时识别线程
    rs485.open("COM3", 9600)         # 打开 485 串口

    # 识别线程内部会自动调用 on_slot_recognized；
    # 串口收到"到位"帧时调用 on_slot_move_done，驱动 worker 继续识别。
"""

import logging

logger = logging.getLogger(__name__)


class RS485Interface:
    """RS485 通信预留接口。

    手动 / 自动模式下均由 ``LiveInspectionWorker`` 在适当时机调用，
    主线程无需感知通信细节。
    """

    def __init__(self):
        # 预留：实现时持有串口对象和绑定的 worker 引用
        self._worker = None
        self.port = None
        self.baudrate = None

    def open(self, port, baudrate=9600):
        """预留打开 RS485 串口接口。

        当前阶段不实际连接硬件，仅记录配置；真实上线时在这里接入 pyserial。
        """
        self.port = port
        self.baudrate = baudrate
        logger.info("[RS485 预留] open port=%s baudrate=%s", port, baudrate)
        return True

    def close(self):
        """预留关闭 RS485 串口接口。"""
        logger.info("[RS485 预留] close port=%s", self.port)
        self.port = None
        self.baudrate = None

    def bind_worker(self, worker):
        """绑定实时识别线程，以便 on_slot_move_done 能触发 worker.confirm_move()。"""
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
        预留接口：此处应通过 RS485 发送指令帧，帧格式待硬件协议确定后填充。
        """
        # TODO: 实现 RS485 发送逻辑，示例：
        #   frame = build_move_command(slot_id, result["status"])
        #   self._serial.write(frame)
        logger.debug("[RS485 预留] on_slot_recognized slot_id=%d result=%s", slot_id, result)

    def on_slot_move_done(self, slot_id):
        """接收外部设备"已到位"指令后回调，触发下一槽位识别。

        Parameters
        ----------
        slot_id : int
            1 基准的槽位编号，表示摄像头已移至该槽位上方。
        """
        # TODO: 从帧中解析 slot_id，校验后再 confirm_move
        logger.debug("[RS485 预留] on_slot_move_done slot_id=%d", slot_id)
        if self._worker is not None:
            self._worker.confirm_move()
