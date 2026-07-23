"""检测结果判定逻辑。

本模块把"OCR 识别结果 + 目标型号"翻译成产品语义：

    正常 / 异常 / 识别失败

业务语义
--------
- 一个槽位只要能识别出任意文本，就不算"识别失败"。
- 能识别出文本但全都不含目标型号 -> "异常"（红）。
- 文本含目标型号 -> "正常"（绿）。
- 完全没有识别到文本 -> "识别失败"（红）。
"""


class MaterialController:
    """纯函数型的检测结果判定器。

    不持有状态、只做一次性的比较，所以所有方法都是 `@staticmethod`。
    """

    @staticmethod
    def _normalize_target_texts(target_model):
        """把单行模板或多行模板统一整理成去重后的大写字符串列表。"""
        if isinstance(target_model, (list, tuple, set)):
            raw_items = target_model
        else:
            raw_items = [target_model]

        targets = []
        for item in raw_items:
            text = str(item or "").strip()
            text_up = text.upper()
            if text_up and text_up not in targets:
                targets.append(text_up)
        return targets

    @staticmethod
    def _recognized_text_candidates(detected_data):
        """返回高置信度识别文本候选；包含未截断的 items，兼容旧的 texts 字段。"""
        candidates = []
        for text in detected_data.get("texts", []) or []:
            clean = str(text or "").strip().upper()
            if clean and clean not in candidates:
                candidates.append(clean)

        for item in detected_data.get("items", []) or []:
            clean = str(item.get("text", "") or "").strip().upper()
            if clean and clean not in candidates:
                candidates.append(clean)
        return candidates

    @staticmethod
    def analyze_status(detected_data, target_model, target_angle):
        """把 OCR 结果与目标参数比对，输出 UI 用的 (文本, 颜色键)。

        Parameters
        ----------
        detected_data : dict
            OCR 引擎返回的结果字典，至少包含：

            - ``texts`` : list[str]  识别出的候选文本列表
            - ``angle`` : int        兼容保留字段；当前判定不再比较角度

        target_model : str
            目标型号字符串（大小写不敏感，两端空白会被去掉）。
        target_angle : int | str
            兼容保留参数；参考图和检测图已在 OCR 前按人工角度归一化。

        Returns
        -------
        tuple[str, str]
            - 第 1 项：中文状态（"正常" / "异常" / "识别失败"）
            - 第 2 项：颜色键（"green" / "red"），供 `MaterialSlot.set_result` 上色
        """
        raw_status = str(detected_data.get("status", ""))
        if raw_status == "empty_slot":
            return "空槽", "red"

        texts = MaterialController._recognized_text_candidates(detected_data)
        # 完全没识别到文字 -> 识别失败
        if not texts:
            return "识别失败", "red"

        targets = MaterialController._normalize_target_texts(target_model)
        if not targets:
            return "异常", "red"

        # 多行模板要求每一行都被识别结果覆盖；单行模板保持原来的包含匹配语义。
        model_match = all(
            any(target in text for text in texts)
            for target in targets
        )

        if model_match:
            return "正常", "green"

        # 识别到文字但不含目标型号 -> 型号错误
        return "型号错误", "red"
