"""OCR 引擎 - 基于 ONNX 的文本识别流水线。

三步管线：
1. **方向分类（cls）**：判断整张图是 0/90/180/270 度中的哪一种，先把图转正。
2. **文本检测（det）**：找出图上可能的文本框，返回多个裁剪小图。
3. **文本识别（rec）**：逐个小图识别文字并返回置信度分数。

关键设计
--------
- `OCREngine` 实例复用：ONNX session 存在类级 `_shared_*` 字段里，多次
  `OCREngine()` 只会在首次加载模型，后续构造只做一次轻量绑定。
- `predict_image` 与 `predict_image_from_array` 一个从文件路径读，一个
  直接拿内存里的 numpy 数组，批量检测时用后者配合预加载可减少 I/O 抖动。
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def get_resource_root() -> Path:
    """返回资源（包含 `onnx/` 目录）的根路径。

    - 冻结态（PyInstaller 打包）：`sys._MEIPASS` 解压根；
    - 开发态：`ocr/` 的父目录，也就是项目根。
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]  # chipocr/


def get_helper_root() -> Path:
    """返回 `ocr_onnx_py/` 辅助包的根路径。

    该路径解析方式和 `get_resource_root` 对齐，但多一层 `ocr_onnx_py` 子目录。
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "ocr_onnx_py"
    return Path(__file__).resolve().parents[1] / "ocr_onnx_py"


# class _LegacyPaddleXEngine:
#     """旧版 PaddleX pipeline 引擎的历史残留。

#     当前代码路径已不再使用，仅作为参考保留。新路径走下面的 `OCREngine`。
#     """

#     def __init__(self):
#         self.pipeline = None

#     def predict_image(self, img_path):
#         """对单张图片跑 PaddleX pipeline（历史实现，已弃用）。"""
#         try:
#             output = self.pipeline.predict(input=img_path, use_doc_orientation_classify=True)
#             for res in output:
#                 raw_texts = res.get("rec_texts", [])
#                 raw_scores = res.get("rec_scores", [])

#                 # 严过滤：只保留 score > 0.9 且长度 > 2 的识别项
#                 valid_texts = []
#                 for text, score in zip(raw_texts, raw_scores):
#                     if score > 0.9 and len(text.strip()) > 2:
#                         valid_texts.append(text.strip())

#                 return {
#                     "angle": int(res.get("doc_preprocessor_res", {}).get("angle", 0)),
#                     "texts": valid_texts,
#                     "status": "success"
#                 }
#         except Exception as e:
#             return {"angle": -1, "texts": [], "status": f"error: {e}"}
#         return {"angle": -1, "texts": [], "status": "empty"}


class OCREngine:
    """OCR 推理引擎（cls + det + rec 三合一）。

    类级共享字段
    -------------
    `_shared_cv2` / `_shared_np` / `_shared_detector` / `_shared_classifier` /
    `_shared_recognizer` 是跨实例共享的；第一个 `OCREngine()` 会加载模型
    把它们填起来，后续构造只是把这些引用绑到 self 上。**不可**把它们降级
    为实例级状态，否则每次构造都会重载模型。

    过滤阈值（产品调参）
    --------------------
    - ``score > 0.5``：识别结果最低置信度
    - ``len(text) > 2``：文本长度下限（太短多半是噪声）
    - ``max_ocr_boxes = 4``：每张图最多送几个候选框进识别器
    - ``max_return_texts = 2``：最多返回几条识别文本

    这些值是产品侧长期调参的结果，请勿随意修改。
    """

    _shared_cv2 = None
    _shared_np = None
    _shared_detector = None
    _shared_classifier = None
    _shared_recognizer = None

    def __init__(self):
        """构造引擎并立即预热（加载模型）。

        加载失败不抛异常，而是把异常塞到 `backend_init_error`，交给后续
        `predict_image` 时再统一处理 —— 这样 UI 启动不会因为模型损坏而崩溃。
        """
        self.resource_root = get_resource_root()
        self.model_dir = self.resource_root / "onnx"
        self.ocr_onnx_py_dir = get_helper_root()

        # 检测/识别相关阈值，见类 docstring
        self.det_resize_long = 512
        self.det_max_candidates = 100
        self.max_ocr_boxes = 4
        self.max_return_texts = 2

        self.cv2 = None
        self.np = None
        self.detector = None
        self.classifier = None
        self.recognizer = None
        self.backend_init_error = None

        # 预热模型：让第一次 predict 不承担冷启动开销
        try:
            self._ensure_backend()
            logger.info("OCR 引擎初始化成功, model_dir=%s", self.model_dir)
        except Exception as e:
            self.backend_init_error = e
            logger.error("OCR 引擎初始化失败: %s", e)

    def _bind_shared_backend(self):
        """把类级共享字段绑到 self 上（零开销）。"""
        cls = type(self)
        self.cv2 = cls._shared_cv2
        self.np = cls._shared_np
        self.detector = cls._shared_detector
        self.classifier = cls._shared_classifier
        self.recognizer = cls._shared_recognizer

    def _ensure_backend(self):
        """确保 ONNX 三件套已加载。

        如果其他实例已经加载过模型，只做一次绑定；否则真正加载 ONNX 模型
        并把 session 写入类级字段，供后续实例共享。

        Raises
        ------
        FileNotFoundError
            模型目录不存在。
        ModuleNotFoundError
            辅助包 `ocr_onnx_py` 或其依赖没装/找不到。
        """
        cls = type(self)
        if (cls._shared_detector is not None
                and cls._shared_classifier is not None
                and cls._shared_recognizer is not None):
            # 模型已加载，只做绑定
            self._bind_shared_backend()
            return

        if not self.model_dir.exists():
            raise FileNotFoundError(f"Cannot find model dir: {self.model_dir}")

        # 把 ocr_onnx_py 动态加到 sys.path，以免用户忘了 pip install -e
        helper_path = str(self.ocr_onnx_py_dir)
        if self.ocr_onnx_py_dir.exists() and helper_path not in sys.path:
            sys.path.insert(0, helper_path)

        try:
            import cv2
            import numpy as np
            from text_cls import TextClassifier
            from text_det import TextDetector
            from text_rec import TextRecognizer
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(
                f"Cannot load OCR helper modules. helper_dir={self.ocr_onnx_py_dir}"
            ) from e

        # 三个 ONNX session：det/cls/rec
        detector = TextDetector(str(self.model_dir / "det" / "inference.onnx"))
        detector.resize_long = self.det_resize_long
        detector.postprocess.max_candidates = self.det_max_candidates
        classifier = TextClassifier(str(self.model_dir / "cls" / "inference.onnx"))
        recognizer = TextRecognizer(
            model_path=str(self.model_dir / "rec" / "inference.onnx"),
            rec_yml_path=str(self.model_dir / "rec" / "inference.yml"),
        )

        # 存到类级字段供共享
        cls._shared_cv2 = cv2
        cls._shared_np = np
        cls._shared_detector = detector
        cls._shared_classifier = classifier
        cls._shared_recognizer = recognizer
        self._bind_shared_backend()

    @staticmethod
    def _parse_angle(label):
        """把分类器输出的 label（可能是 "0"/"90"/"180deg" 等）解析成整数角度。"""
        label = str(label).strip()
        digits = "".join(ch for ch in label if ch.isdigit())
        return int(digits) if digits else 0

    def _rotate_to_upright(self, image, angle):
        """按检测到的角度把图像转成正向。"""
        if angle == 90:
            return self.cv2.rotate(image, self.cv2.ROTATE_90_COUNTERCLOCKWISE)
        if angle == 180:
            return self.cv2.rotate(image, self.cv2.ROTATE_180)
        if angle == 270:
            return self.cv2.rotate(image, self.cv2.ROTATE_90_CLOCKWISE)
        return image

    def _decode_rec_logits_with_score(self, logits):
        """CTC 解码识别头的输出，同时返回平均置信度。

        识别器输出的 shape 一般是 ``(time_steps, vocab_size)``；少数情况
        反过来，需要转置。使用贪心 + "删相邻重复 / 删 blank (index=0)" 的
        标准 CTC 规则。

        Returns
        -------
        tuple[str, float]
            ``(解码文本, 平均字符置信度)``。空结果返回 ``("", 0.0)``。
        """
        if logits.ndim != 2:
            raise ValueError(f"Unexpected rec logits shape: {logits.shape}")

        # 如果 vocab_size > time_steps，认为 shape 反了，转置过来
        if logits.shape[0] <= logits.shape[1]:
            time_steps = logits
        else:
            time_steps = logits.transpose(1, 0)

        # 数值稳定的 softmax
        time_steps = time_steps - self.np.max(time_steps, axis=1, keepdims=True)
        time_probs = self.np.exp(time_steps)
        time_probs = time_probs / self.np.sum(time_probs, axis=1, keepdims=True)

        indices = self.np.argmax(time_probs, axis=1).tolist()
        scores = self.np.max(time_probs, axis=1).tolist()

        # CTC 解码：跳过 blank（index=0）和相邻重复
        text = []
        kept_scores = []
        prev_index = -1
        for index, score in zip(indices, scores):
            if index != 0 and index != prev_index:
                dict_index = index - 1  # rec 字典不含 blank，所以 -1 对齐
                if 0 <= dict_index < len(self.recognizer.characters):
                    text.append(self.recognizer.characters[dict_index])
                    kept_scores.append(float(score))
            prev_index = index

        if not text:
            return "", 0.0

        avg_score = sum(kept_scores) / len(kept_scores) if kept_scores else 0.0
        return "".join(text).strip(), avg_score

    def _predict_text_with_score(self, crop):
        """对单个 crop 跑一次 recognizer，返回 (文本, 置信度)。"""
        input_tensor = self.recognizer.preprocess(crop)
        outputs = self.recognizer.session.run(None, {self.recognizer.input_name: input_tensor})
        output = self.np.asarray(outputs[0])

        if output.ndim != 3 or output.shape[0] != 1:
            raise ValueError(f"Unexpected rec output shape: {output.shape}")
        return self._decode_rec_logits_with_score(output[0])

    def _get_rec_batch_capacity(self, item_count):
        """根据 recognizer 的输入 batch 维度确定一次最多送几张 crop。

        动态 batch（shape[0] 为 -1 / None / 字符串）时返回 ``item_count``；
        固定 batch 时返回 ``min(固定值, item_count)``，至少 1。
        """
        input_shape = getattr(self.recognizer.session.get_inputs()[0], "shape", [])
        if not input_shape:
            return 1

        batch_dim = input_shape[0]
        if isinstance(batch_dim, str) or batch_dim in (None, -1):
            return max(1, item_count)

        try:
            batch_capacity = int(batch_dim)
        except (TypeError, ValueError):
            return 1
        return max(1, min(batch_capacity, item_count))

    def _predict_batch_texts_with_scores(self, crops):
        """批量跑识别器。

        单张走 `_predict_text_with_score`；多张时尝试一次前向；若模型不支持
        动态 batch 抛异常，调用方应当回退到逐张推理。

        Returns
        -------
        list[tuple[str, float]]
            每张 crop 对应的 (文本, 置信度)。
        """
        if not crops:
            return []

        if len(crops) == 1:
            return [self._predict_text_with_score(crops[0])]

        # 把所有 preprocess 结果沿 batch 维度堆叠
        input_tensor = self.np.stack(
            [self.recognizer.preprocess(crop)[0] for crop in crops], axis=0,
        )
        outputs = self.recognizer.session.run(None, {self.recognizer.input_name: input_tensor})
        output = self.np.asarray(outputs[0])

        if output.ndim != 3 or output.shape[0] != len(crops):
            raise ValueError(f"Unexpected batched rec output shape: {output.shape}")

        return [self._decode_rec_logits_with_score(output[index]) for index in range(output.shape[0])]

    def predict_image_from_array(self, image):
        """对已经加载到内存的 BGR numpy 图像跑 OCR。

        Returns
        -------
        dict
            ``{"angle": int, "texts": list[str], "status": str}``。
            ``status`` 取值：``"success"`` / ``"empty"`` / ``"error: ..."``。
        """
        try:
            self._ensure_backend()
            if image is None:
                return {"angle": -1, "texts": [], "status": "error: image is None"}
            return self._predict_core(image)
        except ModuleNotFoundError as e:
            return {"angle": -1, "texts": [], "status": f"error: missing package {e.name}"}
        except Exception as e:
            return {"angle": -1, "texts": [], "status": f"error: {e}"}

    def _predict_core(self, image):
        """核心 OCR 流水线。

        步骤：
        1. 分类器判定原图角度，把图转到正向。
        2. 检测器找文本框并裁剪，按面积降序取前 ``max_ocr_boxes`` 个。
        3. 识别器按 batch 跑，过滤 ``score > 0.5 & len > 2`` 的结果。
        4. 若严过滤后没有结果，再从 fallback 里兜底拿至少一条，避免误报空。
        """
        # 1) 分类 + 转正
        cls_result = self.classifier.predict(image)
        angle = self._parse_angle(cls_result.get("label", 0))
        upright_image = self._rotate_to_upright(image, angle)
        h, w = upright_image.shape[:2]

        # 2) 检测：没框就整图作为候选；有框按面积降序截断
        raw_results = self.detector.detect_and_crop(upright_image)
        logger.info("OCR 检测候选框数量=%d", len(raw_results))
        if not raw_results:
            logger.info("OCR 检测未返回文本框，使用整图作为识别候选")
            raw_results = [{
                "crop": upright_image,
                "box": self.np.array(
                    [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]],
                    dtype=self.np.float32,
                ),
            }]
        else:
            raw_results = sorted(
                raw_results,
                key=lambda item: item["crop"].shape[0] * item["crop"].shape[1],
                reverse=True,
            )[: self.max_ocr_boxes]

        # 3) 识别 + 严过滤 + 兜底
        valid_texts = []       # 达到严阈值的文本
        fallback_texts = []    # 原始去重文本，供兜底使用
        visual_items = []      # 调光预览用：识别文本 + 置信度 + 检测框
        crops = [item["crop"] for item in raw_results]
        batch_capacity = self._get_rec_batch_capacity(len(crops))
        for start in range(0, len(crops), batch_capacity):
            crop_chunk = crops[start : start + batch_capacity]
            try:
                rec_results = self._predict_batch_texts_with_scores(crop_chunk)
            except Exception:
                # 批量失败 -> 逐张退化（比如某些模型不支持动态 batch）
                rec_results = [self._predict_text_with_score(crop) for crop in crop_chunk]

            for offset, (text, score) in enumerate(rec_results):
                clean_text = text.strip()
                if not clean_text:
                    continue

                raw_item = raw_results[start + offset]
                box = raw_item.get("box")
                visual_items.append({
                    "text": clean_text,
                    "score": float(score),
                    "box": box.astype(float).tolist() if box is not None else None,
                })

                if clean_text not in fallback_texts:
                    fallback_texts.append(clean_text)

                logger.info(
                    "OCR 识别候选 text=%r score=%.4f len=%d",
                    clean_text,
                    score,
                    len(clean_text),
                )

                if score > 0.5 and len(clean_text) > 2 and clean_text not in valid_texts:
                    valid_texts.append(clean_text)
                    if len(valid_texts) >= self.max_return_texts:
                        break

            if len(valid_texts) >= self.max_return_texts:
                break

        logger.info("OCR 过滤后文本=%s，兜底文本=%s", valid_texts, fallback_texts)

        # 4) 兜底：严过滤 0 条时，从 fallback 里挑长度 >=2 的先拿
        if not valid_texts:
            for text in fallback_texts:
                if len(text) >= 2:
                    valid_texts.append(text)
                if len(valid_texts) >= self.max_return_texts:
                    break
            # 还是没有？直接取 fallback 第一条，至少让 UI 显示点东西
            if not valid_texts and fallback_texts:
                valid_texts = fallback_texts[:1]

        if not valid_texts:
            return {
                "angle": int(angle),
                "texts": [],
                "items": visual_items,
                "box_coordinate": "upright",
                "image_shape": [int(h), int(w)],
                "status": "empty",
            }

        return {
            "angle": int(angle),
            "texts": valid_texts,
            "items": visual_items,
            "box_coordinate": "upright",
            "image_shape": [int(h), int(w)],
            "status": "success",
        }

    def predict_image(self, img_path):
        """对图片文件路径跑 OCR。

        内部先用 OpenCV 读图再走 `_predict_core`。批量场景建议外层预加载后
        直接调 `predict_image_from_array`，可减少 I/O 抖动。
        """
        try:
            self._ensure_backend()

            image = self.cv2.imread(str(img_path))
            if image is None:
                return {"angle": -1, "texts": [], "status": f"error: cannot read image: {img_path}"}

            return self._predict_core(image)
        except ModuleNotFoundError as e:
            return {"angle": -1, "texts": [], "status": f"error: missing package {e.name}"}
        except Exception as e:
            return {"angle": -1, "texts": [], "status": f"error: {e}"}
