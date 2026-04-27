"""检测结果判定逻辑。

本模块把"OCR 识别结果 + 目标型号/角度"翻译成四态产品语义之一：

    正常 / 方向错误 / 型号错误 / 识别失败

业务语义
--------
- 一个槽位只要能识别出任意文本，就不算"识别失败"。
- 能识别出文本但全都不含目标型号 -> "型号错误"（红）。
- 文本含目标型号且角度匹配 -> "正常"（绿）。
- 文本含目标型号但角度不匹配 -> "方向错误"（红）。
- 完全没有识别到文本 -> "识别失败"（红）。
"""


class MaterialController:
    """纯函数型的检测结果判定器。

    不持有状态、只做一次性的比较，所以所有方法都是 `@staticmethod`。
    """

    @staticmethod
    def analyze_status(detected_data, target_model, target_angle):
        """把 OCR 结果与目标参数比对，输出 UI 用的 (文本, 颜色键)。

        Parameters
        ----------
        detected_data : dict
            OCR 引擎返回的结果字典，至少包含：

            - ``texts`` : list[str]  识别出的候选文本列表
            - ``angle`` : int        识别到的方向角度（0/90/180/270）

        target_model : str
            目标型号字符串（大小写不敏感，两端空白会被去掉）。
        target_angle : int | str
            目标方向角度；会转成 int 再比较。

        Returns
        -------
        tuple[str, str]
            - 第 1 项：中文状态（"正常" / "方向错误" / "型号错误" / "识别失败"）
            - 第 2 项：颜色键（"green" / "red"），供 `MaterialSlot.set_result` 上色
        """
        texts = detected_data.get("texts", [])
        angle = detected_data.get("angle", 0)

        # 完全没识别到文字 -> 识别失败
        if not texts:
            return "识别失败", "red"

        target_up = target_model.upper().strip()
        # 任一候选文本包含目标型号（子串匹配 + 大小写不敏感）
        model_match = any(target_up in text.upper() for text in texts)

        if model_match:
            # 型号对 + 角度对 = 正常；型号对 + 角度错 = 方向错误
            return ("正常", "green") if int(angle) == int(target_angle) else ("方向错误", "red")

        # 识别到文字但不含目标型号 -> 型号错误
        return "型号错误", "red"
