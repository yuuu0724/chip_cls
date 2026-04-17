"""OCR 引擎 - 基于 ONNX 的文本识别"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def get_resource_root() -> Path:
    # PyInstaller bundles data files under sys._MEIPASS at runtime.
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]  # demo/


def get_helper_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "ocr_onnx_py"
    return Path(__file__).resolve().parents[1] / "ocr_onnx_py"


class _LegacyPaddleXEngine:
    def __init__(self):
        self.pipeline = None

    def predict_image(self, img_path):
        try:
            output = self.pipeline.predict(input=img_path, use_doc_orientation_classify=True)
            for res in output:
                # 获取文字、角度以及对应的置信度分数
                raw_texts = res.get("rec_texts", [])
                raw_scores = res.get("rec_scores", [])  # 关键：获取每个词的分数
                
                # 过滤：只有置信度 > 0.5 且长度 > 2 的才保留
                valid_texts = []
                for text, score in zip(raw_texts, raw_scores):
                    if score > 0.9 and len(text.strip()) > 2:
                        valid_texts.append(text.strip())

                return {
                    "angle": int(res.get("doc_preprocessor_res", {}).get("angle", 0)),
                    "texts": valid_texts,
                    "status": "success"
                }
        except Exception as e:
            return {"angle": -1, "texts": [], "status": f"error: {e}"}
        return {"angle": -1, "texts": [], "status": "empty"}


class OCREngine:
    # Share one set of ONNX sessions inside the process.
    # This keeps UI startup logic unchanged, but avoids reloading models
    # when the main window and template manager each create an engine.
    _shared_cv2 = None
    _shared_np = None
    _shared_detector = None
    _shared_classifier = None
    _shared_recognizer = None

    def __init__(self):
        self.resource_root = get_resource_root()
        self.model_dir = self.resource_root / "onnx"
        self.ocr_onnx_py_dir = get_helper_root()
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

        # Warm up once during program startup so later OCR calls reuse the
        # loaded sessions directly. Errors are deferred to predict_image.
        try:
            self._ensure_backend()
            logger.info("OCR 引擎初始化成功, model_dir=%s", self.model_dir)
        except Exception as e:
            self.backend_init_error = e
            logger.error("OCR 引擎初始化失败: %s", e)

    def _bind_shared_backend(self):
        cls = type(self)
        self.cv2 = cls._shared_cv2
        self.np = cls._shared_np
        self.detector = cls._shared_detector
        self.classifier = cls._shared_classifier
        self.recognizer = cls._shared_recognizer

    def _ensure_backend(self):
        cls = type(self)
        if cls._shared_detector is not None and cls._shared_classifier is not None and cls._shared_recognizer is not None:
            self._bind_shared_backend()
            return

        if not self.model_dir.exists():
            raise FileNotFoundError(f"Cannot find model dir: {self.model_dir}")

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

        detector = TextDetector(str(self.model_dir / "det" / "inference.onnx"))
        detector.resize_long = self.det_resize_long
        detector.postprocess.max_candidates = self.det_max_candidates
        classifier = TextClassifier(str(self.model_dir / "cls" / "inference.onnx"))
        recognizer = TextRecognizer(
            model_path=str(self.model_dir / "rec" / "inference.onnx"),
            rec_yml_path=str(self.model_dir / "rec" / "inference.yml"),
        )
        cls._shared_cv2 = cv2
        cls._shared_np = np
        cls._shared_detector = detector
        cls._shared_classifier = classifier
        cls._shared_recognizer = recognizer
        self._bind_shared_backend()

    @staticmethod
    def _parse_angle(label):
        label = str(label).strip()
        digits = "".join(ch for ch in label if ch.isdigit())
        return int(digits) if digits else 0

    def _rotate_to_upright(self, image, angle):
        if angle == 90:
            return self.cv2.rotate(image, self.cv2.ROTATE_90_COUNTERCLOCKWISE)
        if angle == 180:
            return self.cv2.rotate(image, self.cv2.ROTATE_180)
        if angle == 270:
            return self.cv2.rotate(image, self.cv2.ROTATE_90_CLOCKWISE)
        return image

    def _decode_rec_logits_with_score(self, logits):
        if logits.ndim != 2:
            raise ValueError(f"Unexpected rec logits shape: {logits.shape}")

        if logits.shape[0] <= logits.shape[1]:
            time_steps = logits
        else:
            time_steps = logits.transpose(1, 0)

        time_steps = time_steps - self.np.max(time_steps, axis=1, keepdims=True)
        time_probs = self.np.exp(time_steps)
        time_probs = time_probs / self.np.sum(time_probs, axis=1, keepdims=True)

        indices = self.np.argmax(time_probs, axis=1).tolist()
        scores = self.np.max(time_probs, axis=1).tolist()

        text = []
        kept_scores = []
        prev_index = -1
        for index, score in zip(indices, scores):
            if index != 0 and index != prev_index:
                dict_index = index - 1
                if 0 <= dict_index < len(self.recognizer.characters):
                    text.append(self.recognizer.characters[dict_index])
                    kept_scores.append(float(score))
            prev_index = index

        if not text:
            return "", 0.0

        avg_score = sum(kept_scores) / len(kept_scores) if kept_scores else 0.0
        return "".join(text).strip(), avg_score

    def _predict_text_with_score(self, crop):
        input_tensor = self.recognizer.preprocess(crop)
        outputs = self.recognizer.session.run(None, {self.recognizer.input_name: input_tensor})
        output = self.np.asarray(outputs[0])

        if output.ndim != 3 or output.shape[0] != 1:
            raise ValueError(f"Unexpected rec output shape: {output.shape}")
        return self._decode_rec_logits_with_score(output[0])

    def _get_rec_batch_capacity(self, item_count):
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
        if not crops:
            return []

        if len(crops) == 1:
            return [self._predict_text_with_score(crops[0])]

        input_tensor = self.np.stack([self.recognizer.preprocess(crop)[0] for crop in crops], axis=0)
        outputs = self.recognizer.session.run(None, {self.recognizer.input_name: input_tensor})
        output = self.np.asarray(outputs[0])

        if output.ndim != 3 or output.shape[0] != len(crops):
            raise ValueError(f"Unexpected batched rec output shape: {output.shape}")

        return [self._decode_rec_logits_with_score(output[index]) for index in range(output.shape[0])]

    def predict_image_from_array(self, image):
        """Run OCR on an already-loaded numpy image array (BGR)."""
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
        """Core OCR pipeline operating on an already-loaded BGR numpy array."""
        cls_result = self.classifier.predict(image)
        angle = self._parse_angle(cls_result.get("label", 0))
        upright_image = self._rotate_to_upright(image, angle)

        raw_results = self.detector.detect_and_crop(upright_image)
        if not raw_results:
            raw_results = [{"crop": upright_image}]
        else:
            raw_results = sorted(
                raw_results,
                key=lambda item: item["crop"].shape[0] * item["crop"].shape[1],
                reverse=True,
            )[: self.max_ocr_boxes]

        valid_texts = []
        fallback_texts = []
        crops = [item["crop"] for item in raw_results]
        batch_capacity = self._get_rec_batch_capacity(len(crops))
        for start in range(0, len(crops), batch_capacity):
            crop_chunk = crops[start : start + batch_capacity]
            try:
                rec_results = self._predict_batch_texts_with_scores(crop_chunk)
            except Exception:
                rec_results = [self._predict_text_with_score(crop) for crop in crop_chunk]

            for text, score in rec_results:
                clean_text = text.strip()
                if not clean_text:
                    continue

                if clean_text not in fallback_texts:
                    fallback_texts.append(clean_text)

                if score > 0.5 and len(clean_text) > 2 and clean_text not in valid_texts:
                    valid_texts.append(clean_text)
                    if len(valid_texts) >= self.max_return_texts:
                        break

            if len(valid_texts) >= self.max_return_texts:
                break

        if not valid_texts:
            for text in fallback_texts:
                if len(text) >= 2:
                    valid_texts.append(text)
                if len(valid_texts) >= self.max_return_texts:
                    break
            if not valid_texts and fallback_texts:
                valid_texts = fallback_texts[:1]

        if not valid_texts:
            return {"angle": int(angle), "texts": [], "status": "empty"}

        return {
            "angle": int(angle),
            "texts": valid_texts,
            "status": "success",
        }

    def predict_image(self, img_path):
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
