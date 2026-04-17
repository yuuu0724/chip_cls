from __future__ import annotations

import cv2
import numpy as np


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = x - np.max(x, axis=axis, keepdims=True)
    exp_x = np.exp(x)
    return exp_x / np.sum(exp_x, axis=axis, keepdims=True)


def order_points_clockwise(points: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    s = points.sum(axis=1)
    rect[0] = points[np.argmin(s)]
    rect[2] = points[np.argmax(s)]

    diff = np.diff(points, axis=1)
    rect[1] = points[np.argmin(diff)]
    rect[3] = points[np.argmax(diff)]
    return rect


def get_rotate_crop_image(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    points = order_points_clockwise(points.astype(np.float32))

    left = int(np.min(points[:, 0]))
    right = int(np.max(points[:, 0]))
    top = int(np.min(points[:, 1]))
    bottom = int(np.max(points[:, 1]))

    image_crop = image[top : bottom + 1, left : right + 1, :].copy()
    points[:, 0] -= left
    points[:, 1] -= top

    crop_width = int(np.linalg.norm(points[0] - points[1]))
    crop_height = int(np.linalg.norm(points[0] - points[3]))
    crop_width = max(crop_width, 1)
    crop_height = max(crop_height, 1)

    dst_points = np.float32(
        [[0, 0], [crop_width, 0], [crop_width, crop_height], [0, crop_height]]
    )
    matrix = cv2.getPerspectiveTransform(points, dst_points)
    dst_image = cv2.warpPerspective(
        image_crop,
        matrix,
        (crop_width, crop_height),
        borderMode=cv2.BORDER_REPLICATE,
        flags=cv2.INTER_CUBIC,
    )

    # Vertical text lines are rotated once so rec model sees a wide image.
    if dst_image.shape[0] > dst_image.shape[1] * 1.5:
        dst_image = np.rot90(dst_image).copy()
    return dst_image


def sort_boxes(boxes: list[np.ndarray]) -> list[np.ndarray]:
    boxes = sorted(boxes, key=lambda box: (box[0][1], box[0][0]))
    for index in range(len(boxes) - 1):
        current = boxes[index]
        next_box = boxes[index + 1]
        if abs(next_box[0][1] - current[0][1]) < 10 and next_box[0][0] < current[0][0]:
            boxes[index], boxes[index + 1] = boxes[index + 1], boxes[index]
    return boxes


def draw_boxes(image: np.ndarray, boxes: list[np.ndarray]) -> np.ndarray:
    for box in boxes:
        box = box.astype(int)
        cv2.polylines(image, [box], True, (0, 0, 255), 2)
    return image
