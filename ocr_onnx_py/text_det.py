from __future__ import annotations

import logging

import cv2
import numpy as np

from session_utils import create_session
from utils import get_rotate_crop_image, order_points_clockwise, sort_boxes


class TextDetector:
    def __init__(
        self,
        model_path: str,
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
    ) -> None:
        self.logger = logging.getLogger("ocr.text_det")
        self.session = create_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self.input_height, self.input_width = self._resolve_input_size()
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.max_candidates = 100
        self.min_box_size = 3
        self.box_padding_px = 2
        self.logger.info(
            "det YOLO model loaded: %s input_size=%dx%d",
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

    def _normalize_prediction_shape(self, output: np.ndarray) -> np.ndarray:
        pred = np.asarray(output)
        if pred.ndim == 3:
            pred = pred[0]
        if pred.ndim != 2:
            raise ValueError(f"Unexpected det output shape: {pred.shape}")

        # Ultralytics YOLO export commonly returns (4 + classes, anchors).
        if pred.shape[0] < pred.shape[1] and pred.shape[0] <= 256:
            pred = pred.transpose(1, 0)
        if pred.shape[1] < 5:
            raise ValueError(f"Unexpected det output shape: {pred.shape}")
        return pred

    def _prediction_scores(self, pred: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if pred.shape[1] == 5:
            scores = pred[:, 4]
            class_ids = np.zeros(pred.shape[0], dtype=np.int32)
            return scores, class_ids

        class_scores = pred[:, 4:]
        class_ids = np.argmax(class_scores, axis=1).astype(np.int32)
        scores = class_scores[np.arange(class_scores.shape[0]), class_ids]
        return scores, class_ids

    def _parse_predictions(
        self,
        output: np.ndarray,
        scale: float,
        pad_x: int,
        pad_y: int,
        src_w: int,
        src_h: int,
    ) -> list[np.ndarray]:
        pred = self._normalize_prediction_shape(output)
        boxes_xywh = pred[:, :4].astype(np.float32)
        if boxes_xywh.size and float(np.nanmax(boxes_xywh)) <= 2.0:
            boxes_xywh[:, [0, 2]] *= self.input_width
            boxes_xywh[:, [1, 3]] *= self.input_height

        scores, _ = self._prediction_scores(pred)
        raw_boxes = []
        raw_scores = []

        for box_xywh, score in zip(boxes_xywh, scores):
            score = float(score)
            if score < self.conf_threshold:
                continue

            cx, cy, box_w, box_h = box_xywh.astype(float)
            if box_w < self.min_box_size or box_h < self.min_box_size:
                continue

            x1 = (cx - box_w / 2.0 - pad_x - self.box_padding_px) / scale
            y1 = (cy - box_h / 2.0 - pad_y - self.box_padding_px) / scale
            x2 = (cx + box_w / 2.0 - pad_x + self.box_padding_px) / scale
            y2 = (cy + box_h / 2.0 - pad_y + self.box_padding_px) / scale
            clipped = self._clip_box(x1, y1, x2, y2, src_w, src_h)
            if clipped is None:
                continue

            raw_boxes.append(clipped)
            raw_scores.append(score)

        if not raw_boxes:
            return []

        nms_boxes = [
            [x1, y1, x2 - x1, y2 - y1]
            for x1, y1, x2, y2 in raw_boxes
        ]
        keep_indices = cv2.dnn.NMSBoxes(
            nms_boxes,
            raw_scores,
            self.conf_threshold,
            self.iou_threshold,
        )
        if len(keep_indices) == 0:
            return []

        boxes = []
        for index in np.array(keep_indices).reshape(-1):
            x1, y1, x2, y2 = raw_boxes[int(index)]
            box = np.array(
                [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                dtype=np.float32,
            )
            boxes.append(order_points_clockwise(box))
            if len(boxes) >= self.max_candidates:
                break
        return boxes

    def detect(self, image: np.ndarray) -> list[np.ndarray]:
        src_h, src_w = image.shape[:2]
        input_tensor, scale, pad_x, pad_y = self.preprocess(image)
        self.logger.info(
            "det image_shape=%s input_tensor_shape=%s",
            image.shape,
            input_tensor.shape,
        )
        outputs = self.session.run(None, {self.input_name: input_tensor})
        output = np.asarray(outputs[0])
        self.logger.info("det raw_output_shape=%s", output.shape)

        boxes = self._parse_predictions(output, scale, pad_x, pad_y, src_w, src_h)
        self.logger.info("det post_boxes=%d", len(boxes))
        return sort_boxes(boxes)

    def detect_and_crop(self, image: np.ndarray) -> list[dict]:
        results = []
        for box in self.detect(image):
            crop = get_rotate_crop_image(image, box)
            if crop.size == 0:
                continue
            results.append({"box": box, "crop": crop})
        return results
