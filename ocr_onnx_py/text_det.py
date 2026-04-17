from __future__ import annotations

import cv2
import logging
import numpy as np

from session_utils import create_session
from utils import get_rotate_crop_image, order_points_clockwise, sort_boxes

try:
    import pyclipper
except ModuleNotFoundError:
    pyclipper = None


class DBPostProcess:
    def __init__(
        self,
        thresh: float = 0.3,
        box_thresh: float = 0.6,
        max_candidates: int = 1000,
        unclip_ratio: float = 1.5,
    ) -> None:
        self.thresh = thresh
        self.box_thresh = box_thresh
        self.max_candidates = max_candidates
        self.unclip_ratio = unclip_ratio
        self.min_size = 3

    def box_score_fast(self, bitmap: np.ndarray, box: np.ndarray) -> float:
        h, w = bitmap.shape[:2]
        box = box.copy()

        xmin = np.clip(int(np.floor(box[:, 0].min())), 0, w - 1)
        xmax = np.clip(int(np.ceil(box[:, 0].max())), 0, w - 1)
        ymin = np.clip(int(np.floor(box[:, 1].min())), 0, h - 1)
        ymax = np.clip(int(np.ceil(box[:, 1].max())), 0, h - 1)

        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        box[:, 0] -= xmin
        box[:, 1] -= ymin
        cv2.fillPoly(mask, [box.astype(np.int32)], 1)
        return float(cv2.mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)[0])

    def get_mini_boxes(self, contour: np.ndarray) -> tuple[list, float]:
        rect = cv2.minAreaRect(contour)
        points = sorted(cv2.boxPoints(rect).tolist(), key=lambda x: x[0])

        if points[1][1] > points[0][1]:
            index_1, index_4 = 0, 1
        else:
            index_1, index_4 = 1, 0

        if points[3][1] > points[2][1]:
            index_2, index_3 = 2, 3
        else:
            index_2, index_3 = 3, 2

        box = [points[index_1], points[index_2], points[index_3], points[index_4]]
        return box, min(rect[1])

    def unclip(self, box: np.ndarray) -> np.ndarray:
        if pyclipper is None:
            raise ModuleNotFoundError("pyclipper")

        area = abs(cv2.contourArea(box.astype(np.float32)))
        length = cv2.arcLength(box.astype(np.float32).reshape(-1, 1, 2), True)
        if length < 1e-6:
            return np.empty((0, 2), dtype=np.float32)

        distance = area * self.unclip_ratio / length
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(box.astype(np.int32).tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        expanded = offset.Execute(distance)
        if not expanded:
            return np.empty((0, 2), dtype=np.float32)

        expanded = max(expanded, key=lambda item: abs(cv2.contourArea(np.array(item, dtype=np.float32))))
        return np.array(expanded, dtype=np.float32)

    def boxes_from_bitmap(self, pred: np.ndarray, bitmap: np.ndarray, dest_width: int, dest_height: int) -> list:
        contours, _ = cv2.findContours((bitmap * 255).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        num_contours = min(len(contours), self.max_candidates)
        height, width = bitmap.shape

        for index in range(num_contours):
            contour = contours[index].squeeze(1)
            if contour.ndim != 2 or contour.shape[0] < 4:
                continue

            points, short_side = self.get_mini_boxes(contour)
            if short_side < self.min_size:
                continue

            points = np.array(points, dtype=np.float32)
            score = self.box_score_fast(pred, contour.astype(np.float32))
            if score < self.box_thresh:
                continue

            expanded = self.unclip(points)
            if expanded.shape[0] < 4:
                continue

            box, short_side = self.get_mini_boxes(expanded.reshape(-1, 1, 2))
            if short_side < self.min_size + 2:
                continue

            box = np.array(box, dtype=np.float32)
            box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_width), 0, dest_width - 1)
            box[:, 1] = np.clip(np.round(box[:, 1] / height * dest_height), 0, dest_height - 1)
            boxes.append(order_points_clockwise(box))

        return boxes

    def __call__(self, pred: np.ndarray, src_w: int, src_h: int) -> list:
        bitmap = pred > self.thresh
        return self.boxes_from_bitmap(pred, bitmap, src_w, src_h)


class TextDetector:
    def __init__(self, model_path: str) -> None:
        self.logger = logging.getLogger("ocr.text_det")
        self.session = create_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self.resize_long = 960
        self.mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
        self.std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)
        self.postprocess = DBPostProcess()
        self.logger.info("det model loaded: %s", model_path)

    def resize_image(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        scale = self.resize_long / float(max(h, w))
        resize_h = max(int(round(h * scale / 32) * 32), 32)
        resize_w = max(int(round(w * scale / 32) * 32), 32)
        return cv2.resize(image, (resize_w, resize_h))

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        # Keep BGR order because inference.yml uses DecodeImage with img_mode BGR.
        image = self.resize_image(image)
        image = image.astype(np.float32) / 255.0
        image = (image - self.mean) / self.std
        image = image.transpose(2, 0, 1)
        return image[None, :, :, :]

    def detect(self, image: np.ndarray) -> list[np.ndarray]:
        src_h, src_w = image.shape[:2]
        input_tensor = self.preprocess(image)
        self.logger.info("det image_shape=%s input_tensor_shape=%s", image.shape, input_tensor.shape)
        outputs = self.session.run(None, {self.input_name: input_tensor})
        pred = np.asarray(outputs[0])
        self.logger.info("det raw_output_shape=%s", pred.shape)

        if pred.ndim == 4:
            pred = pred[0, 0]
        elif pred.ndim == 3:
            pred = pred[0]
        else:
            raise ValueError(f"Unexpected det output shape: {pred.shape}")

        boxes = self.postprocess(pred, src_w, src_h)
        self.logger.info("det post_boxes=%d", len(boxes))
        return sort_boxes(boxes)

    def detect_and_crop(self, image: np.ndarray) -> list[dict]:
        results = []
        for box in self.detect(image):
            crop = get_rotate_crop_image(image, box)
            results.append({"box": box, "crop": crop})
        return results
