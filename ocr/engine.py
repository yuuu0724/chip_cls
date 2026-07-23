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
from datetime import datetime
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
    - 文本长度不再设下限：短字符也保留给业务层判断
    - ``max_ocr_boxes = 4``：每张图最多送几个候选框进识别器
    - ``max_return_texts = 2``：最多返回几条识别文本

    这些值是产品侧长期调参的结果，请勿随意修改。
    """

    _shared_cv2 = None
    _shared_np = None
    _shared_detector = None
    _shared_classifier = None
    _shared_recognizer = None
    _shared_chip_detector = None
    _shared_chip_detector_error = None

    def __init__(self):
        """构造引擎并立即预热（加载模型）。

        加载失败不抛异常，而是把异常塞到 `backend_init_error`，交给后续
        `predict_image` 时再统一处理 —— 这样 UI 启动不会因为模型损坏而崩溃。
        """
        self.resource_root = get_resource_root()
        self.model_dir = self.resource_root / "onnx"
        self.ocr_onnx_py_dir = get_helper_root()

        # 检测/识别相关阈值，见类 docstring
        self.det_resize_long = 960
        self.det_max_candidates = 100
        self.max_ocr_boxes = 6
        self.max_return_texts = 4
        self.min_rec_score = 0.8
        self.center_chip_require_center_inside_bbox = True
        self.center_chip_inside_margin_px = 5
        self.ocr_roi_input_mode = "raw"
        self.ocr_roi_preprocess_config = self._default_ocr_roi_preprocess_config()
        try:
            from data.config_manager import ConfigManager

            app_config = ConfigManager().get_config()
            self.center_chip_require_center_inside_bbox = bool(
                app_config.get("center_chip_require_center_inside_bbox", True)
            )
            self.center_chip_inside_margin_px = int(
                app_config.get("center_chip_inside_margin_px", 5)
            )
            self.ocr_roi_input_mode = self._normalize_ocr_roi_input_mode(
                app_config.get("ocr_roi_input_mode", "raw")
            )
            self.ocr_roi_preprocess_config = self._normalize_ocr_roi_preprocess_config(
                app_config.get("ocr_roi_preprocess_config")
            )
        except Exception as e:
            logger.warning("读取中心芯片判定配置失败，使用默认值: %s", e)
        self.allowed_chip_chars = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz"
            "0123456789"
            "-./_+:#()[]"
        )

        self.cv2 = None
        self.np = None
        self.detector = None
        self.classifier = None
        self.recognizer = None
        self.chip_detector = None
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
        self.chip_detector = cls._shared_chip_detector

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
        if hasattr(detector, "resize_long"):
            detector.resize_long = self.det_resize_long
        if hasattr(detector, "postprocess"):
            detector.postprocess.max_candidates = self.det_max_candidates
        if hasattr(detector, "max_candidates"):
            detector.max_candidates = self.det_max_candidates
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

    def _ensure_chip_detector(self):
        """按需加载芯片检测模型；加载失败不影响原 OCR 流程。"""
        cls = type(self)
        if cls._shared_chip_detector is not None:
            self.chip_detector = cls._shared_chip_detector
            return self.chip_detector

        chip_model_paths = [
            self.model_dir / "chip" / "chip_best_opset21.onnx",
            self.model_dir / "chip" / "chip_best.onnx",
        ]
        chip_model_path = next((path for path in chip_model_paths if path.exists()), None)
        if chip_model_path is None:
            cls._shared_chip_detector_error = FileNotFoundError(
                ", ".join(str(path) for path in chip_model_paths)
            )
            logger.info(
                "芯片检测模型不存在，跳过 OCR 文本检测: %s",
                [str(path) for path in chip_model_paths],
            )
            return None

        helper_path = str(self.ocr_onnx_py_dir)
        if self.ocr_onnx_py_dir.exists() and helper_path not in sys.path:
            sys.path.insert(0, helper_path)

        try:
            from chip_det import ChipDetector

            detector = ChipDetector(str(chip_model_path))
        except Exception as e:
            cls._shared_chip_detector_error = e
            logger.warning(
                "芯片检测模型加载失败，跳过 OCR 文本检测: model=%s error=%s",
                chip_model_path,
                e,
            )
            return None

        cls._shared_chip_detector = detector
        cls._shared_chip_detector_error = None
        self.chip_detector = detector
        logger.info("芯片检测模型加载成功: %s", chip_model_path)
        return detector

    @staticmethod
    def _offset_box(box, offset_x, offset_y):
        if box is None or (offset_x == 0 and offset_y == 0):
            return box
        adjusted = box.copy()
        adjusted[:, 0] += offset_x
        adjusted[:, 1] += offset_y
        return adjusted

    def _normalize_input_image(self, image):
        """Accept gray/BGR/BGRA images and normalize to BGR for ONNX models."""
        image_shape = getattr(image, "shape", None)
        if image_shape is None or len(image_shape) < 2:
            raise ValueError(f"Invalid image shape: {image_shape}")

        if image.ndim == 2:
            return self.cv2.cvtColor(image, self.cv2.COLOR_GRAY2BGR)

        if image.ndim == 3:
            channels = image.shape[2]
            if channels == 1:
                return self.cv2.cvtColor(image, self.cv2.COLOR_GRAY2BGR)
            if channels == 3:
                return image
            if channels == 4:
                return self.cv2.cvtColor(image, self.cv2.COLOR_BGRA2BGR)

        raise ValueError(f"Unsupported image shape: {image_shape}")

    def _detect_center_chip_candidates(self, image, log_details=True):
        """检测芯片框，并标记离图像中心最近的候选。"""
        detector = self._ensure_chip_detector()
        if detector is None:
            error = type(self)._shared_chip_detector_error
            if log_details and error is not None:
                logger.info("芯片检测不可用，跳过 OCR 文本检测: %s", error)
            return None

        try:
            chips = detector.detect(image)
        except Exception as e:
            logger.warning("芯片检测失败，跳过 OCR 文本检测: %s", e)
            return None

        h, w = image.shape[:2]
        if log_details:
            logger.info("芯片检测候选数量=%d", len(chips))
        if not chips:
            return {
                "chips": [],
                "selected": None,
                "image_shape": [int(h), int(w)],
            }

        image_center_x = w / 2.0
        image_center_y = h / 2.0
        ranked = []
        center_ranked = []
        margin = max(0, int(self.center_chip_inside_margin_px))
        for index, item in enumerate(chips, start=1):
            x1, y1, x2, y2 = item["bbox"]
            center_x = (x1 + x2) / 2.0
            center_y = (y1 + y2) / 2.0
            distance = ((center_x - image_center_x) ** 2 + (center_y - image_center_y) ** 2) ** 0.5
            contains_image_center = (
                x1 - margin <= image_center_x <= x2 + margin
                and y1 - margin <= image_center_y <= y2 + margin
            )
            candidate = dict(item)
            candidate["center"] = [float(center_x), float(center_y)]
            candidate["center_distance"] = float(distance)
            candidate["contains_image_center"] = bool(contains_image_center)
            candidate["selected"] = False
            ranked.append((distance, candidate))
            if contains_image_center or not self.center_chip_require_center_inside_bbox:
                center_ranked.append((distance, candidate))
            if log_details:
                logger.info(
                    "芯片检测框 %d/%d score=%.4f bbox=%s center=(%.1f, %.1f) center_distance=%.2f contains_center=%s",
                    index,
                    len(chips),
                    item.get("score", 0.0),
                    [int(v) for v in item["bbox"]],
                    center_x,
                    center_y,
                    distance,
                    contains_image_center,
                )

        selected = None
        if center_ranked:
            _, selected = min(center_ranked, key=lambda pair: pair[0])
            selected["selected"] = True
        elif log_details:
            logger.info(
                "中心点未落入任何芯片 ROI，判定当前槽位为空；周围候选数量=%d center=(%.1f, %.1f) margin=%d",
                len(chips),
                image_center_x,
                image_center_y,
                margin,
            )
        candidates = [item for _, item in ranked]
        return {
            "chips": candidates,
            "selected": selected,
            "image_shape": [int(h), int(w)],
            "center_empty": selected is None,
        }

    def detect_chip_preview(self, image):
        """给摄像头预览使用的芯片检测结果，不参与 OCR 文本判定。"""
        try:
            self._ensure_backend()
            if image is None:
                return {"chips": [], "selected": None, "status": "error: image is None"}
            image = self._normalize_input_image(image)
            result = self._detect_center_chip_candidates(image, log_details=False)
            if result is None:
                h, w = image.shape[:2]
                return {
                    "chips": [],
                    "selected": None,
                    "image_shape": [int(h), int(w)],
                    "status": "disabled",
                }

            preview_chips = []
            selected_index = -1
            for index, item in enumerate(result["chips"]):
                bbox = [int(v) for v in item["bbox"]]
                chip = {
                    "bbox": bbox,
                    "score": float(item.get("score", 0.0)),
                    "center": [float(v) for v in item.get("center", [0.0, 0.0])],
                    "center_distance": float(item.get("center_distance", 0.0)),
                    "selected": bool(item.get("selected", False)),
                }
                if chip["selected"]:
                    selected_index = index
                preview_chips.append(chip)

            return {
                "chips": preview_chips,
                "selected_index": selected_index,
                "image_shape": result["image_shape"],
                "status": "success" if preview_chips else "empty",
            }
        except Exception as e:
            logger.warning("摄像头预览芯片检测失败: %s", e)
            h, w = image.shape[:2] if image is not None else (0, 0)
            return {
                "chips": [],
                "selected_index": -1,
                "image_shape": [int(h), int(w)],
                "status": f"error: {e}",
            }

    def _select_center_chip_roi(self, image):
        """选择离图像中心最近的芯片 ROI，失败时返回 None 并跳过 OCR 文本检测。"""
        detection = self._detect_center_chip_candidates(image, log_details=True)
        if detection is None:
            return None
        selected = detection["selected"]
        if selected is None:
            return {"empty_slot": True, "candidate_count": len(detection.get("chips", []))}

        x1, y1, x2, y2 = selected["bbox"]
        crop = image[y1 : y2 + 1, x1 : x2 + 1].copy()
        if crop.size == 0:
            logger.warning("芯片检测 ROI 为空，跳过 OCR 文本检测: bbox=%s", selected["bbox"])
            return None

        logger.info(
            "已选择中心最近芯片 ROI bbox=%s crop_shape=%s",
            [int(v) for v in selected["bbox"]],
            crop.shape,
        )
        self._save_chip_roi_preview(crop, selected["bbox"])
        return {
            "crop": crop,
            "bbox": selected["bbox"],
            "box": selected["box"],
        }

    def _save_chip_roi_preview(self, crop, bbox):
        """保存最近一次中心芯片 ROI，便于现场确认 OCR 实际输入。"""
        try:
            preview_dir = self.resource_root / "results" / "chip_roi_preview"
            preview_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            bbox_text = "_".join(str(int(v)) for v in bbox)
            output_path = preview_dir / f"chip_roi_{timestamp}_{bbox_text}.png"
            ok = self.cv2.imwrite(str(output_path), crop)
            if ok:
                logger.info("芯片 ROI 裁剪图已保存: %s", output_path)
            else:
                logger.warning("芯片 ROI 裁剪图保存失败: %s", output_path)
        except Exception as e:
            logger.warning("芯片 ROI 裁剪图保存异常: %s", e)

    def _save_ocr_roi_processed_preview(self, image, bbox):
        """保存进入 det/rec 前的 OCR ROI 处理后图像。"""
        try:
            output_dir = self.resource_root / "results" / "ocr_roi_processed"
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            bbox_text = "_".join(str(int(v)) for v in bbox)
            output_path = output_dir / f"ocr_roi_processed_{timestamp}_{bbox_text}.png"
            ok = self.cv2.imwrite(str(output_path), image)
            if ok:
                logger.info("OCR ROI 处理后图像已保存: %s", output_path)
            else:
                logger.warning("OCR ROI 处理后图像保存失败: %s", output_path)
        except Exception as e:
            logger.warning("OCR ROI 处理后图像保存异常: %s", e)

    def _save_ocr_det_crops(self, raw_results, angle):
        """保存 det 模型裁剪出的文字候选图像。"""
        try:
            output_dir = self.resource_root / "results" / "ocr_det_crops"
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            saved_count = 0
            for index, item in enumerate(raw_results, start=1):
                crop = item.get("crop")
                if crop is None or getattr(crop, "size", 0) == 0:
                    continue
                output_path = output_dir / f"ocr_det_crop_{timestamp}_angle{int(angle):03d}_{index:02d}.png"
                if self.cv2.imwrite(str(output_path), crop):
                    saved_count += 1
                else:
                    logger.warning("OCR det 裁剪图保存失败: %s", output_path)
            if saved_count:
                logger.info("OCR det 裁剪图已保存 %d 张: %s", saved_count, output_dir)
        except Exception as e:
            logger.warning("OCR det 裁剪图保存异常: %s", e)

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

    def _map_rotated_roi_box_to_original(self, box, angle, roi_bbox, roi_shape):
        """把旋转后 ROI 内的文本框坐标映射回原始图坐标。"""
        if box is None:
            return None

        x1, y1, _, _ = roi_bbox
        roi_h, roi_w = roi_shape[:2]
        mapped = box.astype(float).copy()
        if angle == 90:
            x_u = box[:, 0].astype(float).copy()
            y_u = box[:, 1].astype(float).copy()
            mapped[:, 0] = roi_w - 1 - y_u
            mapped[:, 1] = x_u
        elif angle == 180:
            mapped[:, 0] = roi_w - 1 - box[:, 0]
            mapped[:, 1] = roi_h - 1 - box[:, 1]
        elif angle == 270:
            x_u = box[:, 0].astype(float).copy()
            y_u = box[:, 1].astype(float).copy()
            mapped[:, 0] = y_u
            mapped[:, 1] = roi_h - 1 - x_u

        mapped[:, 0] = mapped[:, 0].clip(0, roi_w - 1) + x1
        mapped[:, 1] = mapped[:, 1].clip(0, roi_h - 1) + y1
        return mapped.astype(self.np.float32)

    def _is_chip_text(self, text):
        if not text:
            return False
        return all(ch in self.allowed_chip_chars for ch in text)

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

        # 识别模型有的直接输出概率，有的输出 logits。
        # 若概率分布再次 softmax，几千个字符类别会被摊平，score 会异常接近 0。
        row_sums = self.np.sum(time_steps, axis=1)
        looks_like_probs = (
            self.np.nanmin(time_steps) >= 0.0
            and self.np.nanmax(time_steps) <= 1.0
            and self.np.nanmean(self.np.abs(row_sums - 1.0)) < 1e-2
        )
        if looks_like_probs:
            time_probs = time_steps
        else:
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

    def predict_image_from_array(self, image, target_angle=None):
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
                return {
                    "angle": -1,
                    "texts": [],
                    "all_texts": [],
                    "all_items": [],
                    "items": [],
                    "status": "error: image is None",
                }
            image = self._normalize_input_image(image)
            return self._predict_core(image, target_angle=target_angle)
        except ModuleNotFoundError as e:
            return {
                "angle": -1,
                "texts": [],
                "all_texts": [],
                "all_items": [],
                "items": [],
                "status": f"error: missing package {e.name}",
            }
        except Exception as e:
            return {
                "angle": -1,
                "texts": [],
                "all_texts": [],
                "all_items": [],
                "items": [],
                "status": f"error: {e}",
            }

    def _candidate_angles_from_classifier(self, roi_image):
        """使用 cls 方向模型检测 ROI 角度，只返回一个候选方向。"""
        allowed_angles = {0, 90, 180, 270}
        try:
            cls_result = self.classifier.predict(roi_image)
            label = str(cls_result.get("label", "")).strip()
            angle = self._parse_angle(label) % 360
            score = float(cls_result.get("score", 0.0) or 0.0)
        except Exception as e:
            logger.warning("OCR 方向分类失败，使用 0° 进行识别: %s", e)
            return [0], {
                "status": "error",
                "label": "0",
                "angle": 0,
                "score": 0.0,
                "error": str(e),
            }

        if angle not in allowed_angles:
            logger.warning("OCR 方向分类结果无效，使用 0° 进行识别: label=%r angle=%s", label, angle)
            return [0], {
                "status": "invalid",
                "label": label,
                "angle": 0,
                "score": score,
            }

        logger.info("OCR 方向分类完成 label=%s angle=%d score=%.4f", label, angle, score)
        return [angle], {
            "status": "success",
            "label": label,
            "angle": angle,
            "score": score,
        }

    @staticmethod
    def _normalize_ocr_roi_input_mode(value):
        mode = str(value or "raw").strip().lower()
        if mode in {"raw", "gray", "binary"}:
            return mode
        logger.warning("未知 OCR ROI 输入模式 %r，回退为 raw", value)
        return "raw"

    @staticmethod
    def _default_ocr_roi_preprocess_config():
        return {
            "schema_version": 1,
            "enabled": True,
            "grayscale": True,
            "invert": False,
            "brightness": -96,
            "contrast": 1.43,
            "gamma": 0.81,
            "sharpen": 0.31,
            "filter_enabled": True,
            "filter_type": "非局部均值去噪",
            "filter_kernel": 3,
            "filter_strength": 30.0,
            "clahe_enabled": False,
            "binary_enabled": False,
            "remove_small_enabled": False,
            "morphology_enabled": False,
            "canny_enabled": False,
        }

    @classmethod
    def _normalize_ocr_roi_preprocess_config(cls, value):
        config = cls._default_ocr_roi_preprocess_config()
        if isinstance(value, dict):
            config.update(value)
        return config

    @staticmethod
    def _config_bool(config, key, default=False):
        value = config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "是", "启用"}
        return bool(value)

    @staticmethod
    def _odd_kernel(value, default=3):
        try:
            kernel = int(value)
        except (TypeError, ValueError):
            kernel = default
        kernel = max(1, kernel)
        if kernel % 2 == 0:
            kernel += 1
        return kernel

    def _clip_uint8(self, image):
        return self.np.clip(image, 0, 255).astype(self.np.uint8)

    @staticmethod
    def _float_config(config, key, default):
        try:
            return float(config.get(key, default) or default)
        except (TypeError, ValueError):
            return float(default)

    def _apply_gamma(self, image, gamma):
        try:
            gamma = float(gamma)
        except (TypeError, ValueError):
            gamma = 1.0
        if gamma <= 0 or abs(gamma - 1.0) < 1e-6:
            return image

        table = self.np.array(
            [((index / 255.0) ** gamma) * 255 for index in range(256)],
            dtype=self.np.uint8,
        )
        return self.cv2.LUT(image, table)

    def _apply_ocr_roi_preprocess(self, roi_image):
        config = self.ocr_roi_preprocess_config or {}
        if not self._config_bool(config, "enabled", False):
            return roi_image

        image = roi_image
        if self._config_bool(config, "grayscale", True):
            image = self.cv2.cvtColor(image, self.cv2.COLOR_BGR2GRAY)
        else:
            image = image.copy()

        if self._config_bool(config, "invert", False):
            image = self.cv2.bitwise_not(image)

        brightness = float(config.get("brightness", 0) or 0)
        contrast = float(config.get("contrast", 1.0) or 1.0)
        image = self._clip_uint8(image.astype(self.np.float32) * contrast + brightness)
        image = self._apply_gamma(image, config.get("gamma", 1.0))

        sharpen = float(config.get("sharpen", 0.0) or 0.0)
        if sharpen > 0:
            blurred = self.cv2.GaussianBlur(image, (0, 0), 1.0)
            image = self.cv2.addWeighted(image, 1.0 + sharpen, blurred, -sharpen, 0)

        filter_type = str(config.get("filter_type", "") or "")
        if self._config_bool(config, "filter_enabled", False) and "非局部均值" in filter_type:
            strength = float(config.get("filter_strength", 30.0) or 30.0)
            kernel = self._odd_kernel(config.get("filter_kernel", 3), default=3)
            search_window = max(7, kernel * 7)
            if search_window % 2 == 0:
                search_window += 1
            if image.ndim == 2:
                image = self.cv2.fastNlMeansDenoising(
                    image,
                    None,
                    h=strength,
                    templateWindowSize=kernel,
                    searchWindowSize=search_window,
                )
            else:
                image = self.cv2.fastNlMeansDenoisingColored(
                    image,
                    None,
                    h=strength,
                    hColor=strength,
                    templateWindowSize=kernel,
                    searchWindowSize=search_window,
                )

        if image.ndim == 2:
            image = self.cv2.cvtColor(image, self.cv2.COLOR_GRAY2BGR)

        logger.info(
            "OCR ROI 图像处理完成 shape=%s grayscale=%s brightness=%s contrast=%s gamma=%s sharpen=%s filter=%s kernel=%s strength=%s",
            image.shape,
            self._config_bool(config, "grayscale", True),
            config.get("brightness", 0),
            config.get("contrast", 1.0),
            config.get("gamma", 1.0),
            config.get("sharpen", 0.0),
            config.get("filter_type", ""),
            config.get("filter_kernel", 3),
            config.get("filter_strength", 30.0),
        )
        return image

    def _describe_ocr_roi_processing(self):
        config = self.ocr_roi_preprocess_config or {}
        if self._config_bool(config, "enabled", False):
            steps = []
            if self._config_bool(config, "grayscale", True):
                steps.append("灰度化")
            if self._config_bool(config, "invert", False):
                steps.append("反色")

            brightness = self._float_config(config, "brightness", 0.0)
            contrast = self._float_config(config, "contrast", 1.0)
            if abs(brightness) > 1e-6 or abs(contrast - 1.0) > 1e-6:
                steps.append(f"亮度 {brightness:g} / 对比度 {contrast:g}")

            gamma = self._float_config(config, "gamma", 1.0)
            if gamma > 0 and abs(gamma - 1.0) > 1e-6:
                steps.append(f"Gamma {gamma:g}")

            sharpen = self._float_config(config, "sharpen", 0.0)
            if sharpen > 0:
                steps.append(f"锐化 {sharpen:g}")

            filter_type = str(config.get("filter_type", "") or "")
            if self._config_bool(config, "filter_enabled", False) and "非局部均值" in filter_type:
                kernel = self._odd_kernel(config.get("filter_kernel", 3), default=3)
                strength = self._float_config(config, "filter_strength", 30.0)
                steps.append(f"{filter_type} kernel={kernel} strength={strength:g}")

            return steps or ["启用图像处理但未配置有效步骤"]

        mode = self.ocr_roi_input_mode
        if mode == "gray":
            return ["简易灰度化"]
        if mode == "binary":
            return ["简易灰度化", "Otsu 二值化"]
        return []

    def _prepare_ocr_roi_input(self, roi_image):
        """按配置处理 OCR ROI，并保持三通道 BGR 输入。"""
        if self._config_bool(self.ocr_roi_preprocess_config, "enabled", False):
            try:
                return self._apply_ocr_roi_preprocess(roi_image)
            except Exception as e:
                logger.warning("OCR ROI 图像处理失败，回退简易模式: %s", e)

        mode = self.ocr_roi_input_mode
        if mode == "raw":
            return roi_image

        try:
            gray = self.cv2.cvtColor(roi_image, self.cv2.COLOR_BGR2GRAY)
            if mode == "binary":
                _, gray = self.cv2.threshold(
                    gray,
                    0,
                    255,
                    self.cv2.THRESH_BINARY + self.cv2.THRESH_OTSU,
                )
            return self.cv2.cvtColor(gray, self.cv2.COLOR_GRAY2BGR)
        except Exception as e:
            logger.warning("OCR ROI 输入预处理失败，回退原图: mode=%s error=%s", mode, e)
            return roi_image

    @staticmethod
    def _candidate_angles_for_manual_reference(target_angle):
        """演示模式：按人工参考角度决定 OCR 归一化方向。"""
        try:
            angle = int(target_angle) % 360
        except (TypeError, ValueError):
            logger.warning("人工参考角度无效，回退测试 0/270 两方向: target_angle=%r", target_angle)
            return [0, 270]

        if angle in (0, 270):
            return [angle]

        logger.warning("人工参考角度不是 0/270，回退测试 0/270 两方向: target_angle=%r", target_angle)
        return [0, 270]

    def _recognize_rotated_chip_roi(self, roi_image, roi_bbox, angle, image_shape):
        """对单个旋转方向的中心芯片 ROI 跑 OCR，并返回可排序的结果。"""
        ocr_image = self._rotate_to_upright(roi_image, angle)
        logger.info(
            "方向候选 %d° OCR 输入 ROI shape=%s mode=%s",
            angle,
            ocr_image.shape,
            self.ocr_roi_input_mode,
        )

        raw_results = self.detector.detect_and_crop(ocr_image)
        logger.info("方向候选 %d° OCR 检测候选框数量=%d", angle, len(raw_results))
        for box_index, item in enumerate(raw_results, start=1):
            crop = item.get("crop")
            box = item.get("box")
            original_box = self._map_rotated_roi_box_to_original(
                box, angle, roi_bbox, roi_image.shape
            )
            item["box"] = original_box
            box_points = original_box.astype(float).round(1).tolist() if original_box is not None else None
            logger.info(
                "方向候选 %d° OCR 原始检测框 %d/%d crop_shape=%s box=%s",
                angle,
                box_index,
                len(raw_results),
                getattr(crop, "shape", None),
                box_points,
            )

        if raw_results:
            self._save_ocr_det_crops(raw_results, angle)

        if not raw_results:
            logger.info("方向候选 %d° OCR 检测未返回文本框，使用该方向 ROI 作为识别候选", angle)
            roi_h, roi_w = ocr_image.shape[:2]
            x1, y1, _, _ = roi_bbox
            raw_results = [{
                "crop": ocr_image,
                "box": self.np.array(
                    [
                        [x1, y1],
                        [x1 + roi_w - 1, y1],
                        [x1 + roi_w - 1, y1 + roi_h - 1],
                        [x1, y1 + roi_h - 1],
                    ],
                    dtype=self.np.float32,
                ),
            }]
        else:
            raw_results = sorted(
                raw_results,
                key=lambda item: item["crop"].shape[0] * item["crop"].shape[1],
                reverse=True,
            )[: self.max_ocr_boxes]

        valid_texts = []
        fallback_texts = []
        all_items = []
        visual_items = []
        raw_scores = []
        valid_scores = []

        for box_index, item in enumerate(raw_results, start=1):
            crop = item.get("crop")
            box = item.get("box")
            box_points = box.astype(float).round(1).tolist() if box is not None else None
            logger.info(
                "方向候选 %d° OCR 检测框 %d/%d crop_shape=%s box=%s",
                angle,
                box_index,
                len(raw_results),
                getattr(crop, "shape", None),
                box_points,
            )

        crops = [item["crop"] for item in raw_results]
        batch_capacity = self._get_rec_batch_capacity(len(crops))
        for start in range(0, len(crops), batch_capacity):
            crop_chunk = crops[start : start + batch_capacity]
            try:
                rec_results = self._predict_batch_texts_with_scores(crop_chunk)
            except Exception:
                rec_results = [self._predict_text_with_score(crop) for crop in crop_chunk]

            for offset, (text, score) in enumerate(rec_results):
                item_index = start + offset
                raw_item = raw_results[item_index]
                box = raw_item.get("box")
                clean_text = text.strip()
                box_points = box.astype(float).round(1).tolist() if box is not None else None
                logger.info(
                    "方向候选 %d° OCR 检测框 %d/%d 识别原始 text=%r score=%.4f len=%d crop_shape=%s box=%s",
                    angle,
                    item_index + 1,
                    len(raw_results),
                    clean_text,
                    score,
                    len(clean_text),
                    getattr(raw_item.get("crop"), "shape", None),
                    box_points,
                )
                if not clean_text:
                    continue

                all_items.append({
                    "text": clean_text,
                    "score": float(score),
                    "box": box.astype(float).tolist() if box is not None else None,
                })

                if not self._is_chip_text(clean_text):
                    logger.info("方向候选 %d° OCR 候选非芯片字符，已过滤 text=%r", angle, clean_text)
                    continue

                raw_scores.append(float(score))
                if clean_text not in fallback_texts:
                    fallback_texts.append(clean_text)

                if score < self.min_rec_score:
                    logger.info(
                        "方向候选 %d° OCR 候选置信度低于 %.2f，已过滤 text=%r score=%.4f",
                        angle,
                        self.min_rec_score,
                        clean_text,
                        score,
                    )
                    continue

                visual_items.append({
                    "text": clean_text,
                    "score": float(score),
                    "box": box.astype(float).tolist() if box is not None else None,
                })
                valid_scores.append(float(score))

                logger.info(
                    "方向候选 %d° OCR 识别候选 text=%r score=%.4f len=%d",
                    angle,
                    clean_text,
                    score,
                    len(clean_text),
                )

                if clean_text not in valid_texts:
                    valid_texts.append(clean_text)

        valid_line_count = len(valid_scores)
        total_valid_score = sum(valid_scores)
        best_valid_score = max(valid_scores) if valid_scores else 0.0
        best_raw_score = max(raw_scores) if raw_scores else 0.0
        avg_valid_score = total_valid_score / valid_line_count if valid_scores else 0.0
        valid_texts = valid_texts[: self.max_return_texts]
        score_tuple = (
            1 if valid_line_count else 0,
            total_valid_score,
            avg_valid_score,
            best_valid_score,
            valid_line_count,
            best_raw_score,
            len(fallback_texts),
        )
        logger.info(
            "方向候选 %d° OCR 汇总 texts=%s fallback=%s total_valid=%.4f avg_valid=%.4f best_valid=%.4f valid_lines=%d best_raw=%.4f score_key=%s",
            angle,
            valid_texts,
            fallback_texts,
            total_valid_score,
            avg_valid_score,
            best_valid_score,
            valid_line_count,
            best_raw_score,
            score_tuple,
        )

        return {
            "angle": int(angle),
            "texts": valid_texts,
            "all_texts": fallback_texts,
            "all_items": all_items,
            "items": visual_items,
            "box_coordinate": "original",
            "image_shape": [int(image_shape[0]), int(image_shape[1])],
            "status": "success" if valid_texts else "empty",
            "score_key": score_tuple,
            "orientation_scores": {
                "valid_text_count": len(valid_texts),
                "valid_line_count": valid_line_count,
                "total_valid_score": total_valid_score,
                "best_valid_score": best_valid_score,
                "avg_valid_score": avg_valid_score,
                "best_raw_score": best_raw_score,
                "raw_text_count": len(fallback_texts),
            },
            "selected_chip_bbox": [int(v) for v in roi_bbox],
        }

    def _predict_core(self, image, target_angle=None):
        """核心 OCR 流水线：只裁剪中心芯片 ROI，并按人工参考角度归一化后识别。"""
        h, w = image.shape[:2]
        chip_roi = self._select_center_chip_roi(image)
        if chip_roi is None:
            logger.info("未选中芯片 ROI，跳过 OCR 文本检测，避免整图 OCR shape=%s", image.shape)
            return {
                "angle": 0,
                "texts": [],
                "all_texts": [],
                "all_items": [],
                "items": [],
                "box_coordinate": "original",
                "image_shape": [int(h), int(w)],
                "status": "empty",
            }
        if chip_roi.get("empty_slot"):
            logger.info(
                "中心槽位为空，跳过 OCR 文本检测，周围候选数量=%d",
                chip_roi.get("candidate_count", 0),
            )
            return {
                "angle": 0,
                "texts": [],
                "all_texts": [],
                "all_items": [],
                "items": [],
                "box_coordinate": "original",
                "image_shape": [int(h), int(w)],
                "status": "empty_slot",
            }

        roi_bbox = chip_roi["bbox"]
        roi_image = chip_roi["crop"]
        candidate_angles = self._candidate_angles_for_manual_reference(target_angle)
        orientation_result = {
            "status": "manual_reference",
            "label": "",
            "angle": candidate_angles[0] if len(candidate_angles) == 1 else None,
            "score": 0.0,
            "target_angle": target_angle,
            "reason": "演示模式按人工参考角度归一化：0 不旋转，270 顺时针旋转 90 度",
        }
        ocr_roi_image = self._prepare_ocr_roi_input(roi_image)
        self._save_ocr_roi_processed_preview(ocr_roi_image, roi_bbox)
        preprocess_steps = self._describe_ocr_roi_processing()
        logger.info(
            "OCR 输入已裁剪为中心芯片 ROI bbox=%s shape=%s，处理后 shape=%s，人工参考角度=%s，将测试方向=%s",
            [int(v) for v in roi_bbox],
            roi_image.shape,
            ocr_roi_image.shape,
            target_angle,
            candidate_angles,
        )
        if preprocess_steps:
            logger.info("OCR ROI 已先做图像处理，再按人工参考角度归一化识别 steps=%s", preprocess_steps)
        else:
            logger.info("OCR ROI 未启用图像处理，使用原图按人工参考角度归一化识别")

        candidates = [
            self._recognize_rotated_chip_roi(ocr_roi_image, roi_bbox, angle, (h, w))
            for angle in candidate_angles
        ]
        selected = max(candidates, key=lambda item: item["score_key"])
        selected["preprocess_steps"] = preprocess_steps
        selected["orientation_result"] = orientation_result
        selected["orientation_candidates"] = [
            {
                "angle": item["angle"],
                "status": item["status"],
                "texts": item["texts"],
                "all_texts": item["all_texts"],
                "score_key": list(item["score_key"]),
                "orientation_scores": item["orientation_scores"],
            }
            for item in candidates
        ]
        logger.info(
            "已选择方向候选 %d° 作为当前识别结果 texts=%s all_texts=%s score_key=%s preprocess_steps=%s",
            selected["angle"],
            selected["texts"],
            selected["all_texts"],
            selected["score_key"],
            preprocess_steps,
        )
        selected.pop("score_key", None)
        return selected

    def predict_image(self, img_path, target_angle=None):
        """对图片文件路径跑 OCR。

        内部先用 OpenCV 读图再走 `_predict_core`。批量场景建议外层预加载后
        直接调 `predict_image_from_array`，可减少 I/O 抖动。
        """
        try:
            self._ensure_backend()

            image = self.cv2.imread(str(img_path))
            if image is None:
                return {
                    "angle": -1,
                    "texts": [],
                    "all_texts": [],
                    "all_items": [],
                    "items": [],
                    "status": f"error: cannot read image: {img_path}",
                }

            return self._predict_core(image, target_angle=target_angle)
        except ModuleNotFoundError as e:
            return {
                "angle": -1,
                "texts": [],
                "all_texts": [],
                "all_items": [],
                "items": [],
                "status": f"error: missing package {e.name}",
            }
        except Exception as e:
            return {
                "angle": -1,
                "texts": [],
                "all_texts": [],
                "all_items": [],
                "items": [],
                "status": f"error: {e}",
            }
