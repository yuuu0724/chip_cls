from __future__ import annotations

import cv2
import logging
import numpy as np

from dict_loader import load_rec_dict_from_yml
from session_utils import create_session


class TextRecognizer:
    def __init__(self, model_path: str, rec_yml_path: str) -> None:
        self.logger = logging.getLogger("ocr.text_rec")
        self.session = create_session(model_path)
        self.input_name = self.session.get_inputs()[0].name
        self.characters = load_rec_dict_from_yml(rec_yml_path)
        self.input_width = 320
        self.input_height = 48
        self.logger.info("rec model loaded: %s", model_path)
        self.logger.info("rec dictionary_size=%d", len(self.characters))

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        ratio = w / float(h)
        resized_w = int(np.ceil(self.input_height * ratio))
        resized_w = max(1, min(resized_w, self.input_width))

        resized = cv2.resize(image, (resized_w, self.input_height))
        resized = resized.astype(np.float32) / 255.0
        resized = (resized - 0.5) / 0.5
        resized = resized.transpose(2, 0, 1)

        padded = np.zeros((3, self.input_height, self.input_width), dtype=np.float32)
        padded[:, :, :resized_w] = resized
        return padded[None, :, :, :]

    def _decode(self, output: np.ndarray) -> str:
        if output.ndim != 3 or output.shape[0] != 1:
            raise ValueError(f"Unexpected rec output shape: {output.shape}")

        logits = output[0]
        # Do not hardcode class count from dict here.
        # A much safer rule is: time dimension is usually the smaller one.
        if logits.shape[0] <= logits.shape[1]:
            time_steps = logits
            layout = "BTC"
        else:
            time_steps = logits.transpose(1, 0)
            layout = "BCT"

        class_count = int(time_steps.shape[1])
        expected_class_count = len(self.characters) + 1
        self.logger.info(
            "rec decode raw_shape=%s layout=%s time_steps=%d class_count=%d expected_class_count=%d",
            output.shape,
            layout,
            time_steps.shape[0],
            class_count,
            expected_class_count,
        )
        if class_count != expected_class_count:
            self.logger.warning(
                "rec class count mismatch: model=%d dict_plus_blank=%d",
                class_count,
                expected_class_count,
            )

        indices = np.argmax(time_steps, axis=1).tolist()
        text = []
        prev_index = -1
        unknown_count = 0
        for index in indices:
            if index != 0 and index != prev_index:
                dict_index = index - 1
                if 0 <= dict_index < len(self.characters):
                    text.append(self.characters[dict_index])
                else:
                    unknown_count += 1
                    text.append("?")
            prev_index = index
        if unknown_count > 0:
            self.logger.warning("rec decode unknown_count=%d", unknown_count)
        return "".join(text)

    def predict(self, image: np.ndarray) -> str:
        input_tensor = self.preprocess(image)
        self.logger.info("rec image_shape=%s input_tensor_shape=%s", image.shape, input_tensor.shape)
        outputs = self.session.run(None, {self.input_name: input_tensor})
        output = np.asarray(outputs[0])
        self.logger.info("rec raw_output_shape=%s", output.shape)
        return self._decode(output)
