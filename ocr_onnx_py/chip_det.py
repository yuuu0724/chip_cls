from __future__ import annotations

import logging

import cv2
import numpy as np

from session_utils import create_session


class ChipDetector:
    """基于 YOLOv8 ONNX 的芯片检测器。

    `onnx/chip/chip_best.onnx` 导出参数里标记为 Ultralytics detect 模型，
    输入尺寸 640x640，输出未包含 NMS，因此这里完成 letterbox、置信度过滤
    与 NMS 后处理。
    """

    def __init__(
        self,
        model_path: str,
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
    ) -> None:
        self.logger = logging.getLogger("ocr.chip_det")
        self.session = create_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self.input_height, self.input_width = self._resolve_input_size()
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.logger.info(
            "chip model loaded: %s input_size=%dx%d",
            model_path,
            self.input_width,
            self.input_height,
        )

    def _resolve_input_size(self) -> tuple[int, int]:
        input_shape = getattr(self.session.get_inputs()[0], "shape", [])
        if len(input_shape) == 4:
            height = input_shape[2]
            width = input_shape[3]
            if isinstance(height, int) and isinstance(width, int):
                return height, width
        return 640, 640

    def _letterbox(self, image: np.ndarray) -> tuple[np.ndarray, float, int, int]:
        src_h, src_w = image.shape[:2]
        scale = min(self.input_width / float(src_w), self.input_height / float(src_h))
        new_w = max(int(round(src_w * scale)), 1)
        new_h = max(int(round(src_h * scale)), 1)

        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        padded = np.full(
            (self.input_height, self.input_width, 3),
            114,
            dtype=np.uint8,
        )
        pad_x = (self.input_width - new_w) // 2
        pad_y = (self.input_height - new_h) // 2
        padded[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
        return padded, scale, pad_x, pad_y

    def preprocess(self, image: np.ndarray) -> tuple[np.ndarray, float, int, int]:
        padded, scale, pad_x, pad_y = self._letterbox(image)
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        tensor = rgb.astype(np.float32) / 255.0
        tensor = tensor.transpose(2, 0, 1)
        return tensor[None, :, :, :], scale, pad_x, pad_y

    @staticmethod
    def _clip_box(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: int,
        height: int,
    ) -> list[int] | None:
        x1 = int(round(np.clip(x1, 0, width - 1)))
        y1 = int(round(np.clip(y1, 0, height - 1)))
        x2 = int(round(np.clip(x2, 0, width - 1)))
        y2 = int(round(np.clip(y2, 0, height - 1)))
        if x2 <= x1 or y2 <= y1:
            return None
        return [x1, y1, x2, y2]

    def _parse_predictions(
        self,
        output: np.ndarray,
        scale: float,
        pad_x: int,
        pad_y: int,
        src_w: int,
        src_h: int,
    ) -> list[dict]:
        pred = np.asarray(output)
        if pred.ndim == 3:
            pred = pred[0]
        if pred.ndim != 2:
            raise ValueError(f"Unexpected chip output shape: {pred.shape}")

        # Ultralytics detect 导出常见形状为 (4 + class_count, anchors)。
        if pred.shape[0] < pred.shape[1] and pred.shape[0] <= 128:
            pred = pred.transpose(1, 0)
        if pred.shape[1] < 5:
            raise ValueError(f"Unexpected chip output shape: {pred.shape}")

        raw_boxes = []
        scores = []
        class_ids = []
        class_scores = pred[:, 4:]
        best_class_ids = np.argmax(class_scores, axis=1)
        best_scores = class_scores[np.arange(class_scores.shape[0]), best_class_ids]

        for row, score, class_id in zip(pred, best_scores, best_class_ids):
            score = float(score)
            if score < self.conf_threshold:
                continue

            cx, cy, box_w, box_h = row[:4].astype(float)
            x1 = (cx - box_w / 2.0 - pad_x) / scale
            y1 = (cy - box_h / 2.0 - pad_y) / scale
            x2 = (cx + box_w / 2.0 - pad_x) / scale
            y2 = (cy + box_h / 2.0 - pad_y) / scale
            clipped = self._clip_box(x1, y1, x2, y2, src_w, src_h)
            if clipped is None:
                continue

            raw_boxes.append(clipped)
            scores.append(score)
            class_ids.append(int(class_id))

        if not raw_boxes:
            return []

        nms_boxes = [
            [x1, y1, x2 - x1, y2 - y1]
            for x1, y1, x2, y2 in raw_boxes
        ]
        keep_indices = cv2.dnn.NMSBoxes(
            nms_boxes,
            scores,
            self.conf_threshold,
            self.iou_threshold,
        )
        if len(keep_indices) == 0:
            return []

        results = []
        for index in np.array(keep_indices).reshape(-1):
            x1, y1, x2, y2 = raw_boxes[int(index)]
            box = np.array(
                [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                dtype=np.float32,
            )
            results.append({
                "box": box,
                "bbox": [x1, y1, x2, y2],
                "score": float(scores[int(index)]),
                "class_id": int(class_ids[int(index)]),
                "label": "chip",
            })
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def detect(self, image: np.ndarray) -> list[dict]:
        src_h, src_w = image.shape[:2]
        input_tensor, scale, pad_x, pad_y = self.preprocess(image)
        self.logger.info(
            "chip image_shape=%s input_tensor_shape=%s",
            image.shape,
            input_tensor.shape,
        )
        outputs = self.session.run(None, {self.input_name: input_tensor})
        output = np.asarray(outputs[0])
        self.logger.info("chip raw_output_shape=%s", output.shape)
        results = self._parse_predictions(output, scale, pad_x, pad_y, src_w, src_h)
        self.logger.info("chip post_boxes=%d", len(results))
        return results
