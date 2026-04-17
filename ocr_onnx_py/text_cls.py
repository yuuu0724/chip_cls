from __future__ import annotations

import cv2
import logging
import numpy as np

from session_utils import create_session
from utils import softmax


class TextClassifier:
    def __init__(self, model_path: str) -> None:
        self.logger = logging.getLogger("ocr.text_cls")
        self.session = create_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        # Keep labels aligned with cls/inference.yml.
        self.labels = ["0", "90", "180", "270"]
        self.resize_short = 256
        self.crop_size = 224
        self.mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
        self.std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)
        self.logger.info("cls model loaded: %s", model_path)

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        # New cls model preprocess:
        # 1) resize the short side to 256 while keeping aspect ratio
        # 2) center crop to 224x224
        h, w = image.shape[:2]
        short_side = min(h, w)
        if short_side <= 0:
            raise ValueError(f"Invalid cls image shape: {image.shape}")

        scale = self.resize_short / float(short_side)
        resized_w = max(int(round(w * scale)), self.crop_size)
        resized_h = max(int(round(h * scale)), self.crop_size)
        image = cv2.resize(image, (resized_w, resized_h))

        start_x = max((resized_w - self.crop_size) // 2, 0)
        start_y = max((resized_h - self.crop_size) // 2, 0)
        image = image[start_y : start_y + self.crop_size, start_x : start_x + self.crop_size]

        image = image.astype(np.float32) / 255.0
        image = (image - self.mean) / self.std
        image = image.transpose(2, 0, 1)
        return image[None, :, :, :]

    def predict(self, image: np.ndarray) -> dict:
        input_tensor = self.preprocess(image)
        self.logger.info("cls input_tensor_shape=%s", input_tensor.shape)
        outputs = self.session.run(None, {self.input_name: input_tensor})
        logits = np.asarray(outputs[0])[0]
        self.logger.info("cls output_shape=%s", np.asarray(outputs[0]).shape)
        probs = softmax(logits, axis=-1)
        best_index = int(np.argmax(probs))
        if best_index >= len(self.labels):
            raise ValueError(f"Unexpected cls class index: {best_index}, output_shape={np.asarray(outputs[0]).shape}")
        return {
            "label": self.labels[best_index],
            "score": float(probs[best_index]),
        }
