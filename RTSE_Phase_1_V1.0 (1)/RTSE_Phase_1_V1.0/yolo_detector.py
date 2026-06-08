"""
yolo_detector.py
Drop-in replacement for token_detector.detect_tokens().
Uses YOLOv8n ONNX model when token_model.onnx is present.
Falls back to HSV detection automatically if model is missing.
Classes: 0=green, 1=red, 2=yellow
"""
import os
import cv2
import numpy as np
from token_detector import detect_tokens as hsv_detect_tokens
from token_detector import detect_brightness, get_lane, annotate_frame
MODEL_PATH = 'token_model.onnx'
MODEL_AVAILABLE = os.path.exists(MODEL_PATH)
_net = None
if MODEL_AVAILABLE:
    _net = cv2.dnn.readNetFromONNX(MODEL_PATH)
    print(f"[YOLO] Model loaded from {MODEL_PATH}")
else:
    print("[YOLO] token_model.onnx not found — using HSV fallback")
CLASS_NAMES = {0: 'green', 1: 'red', 2: 'yellow'}
INPUT_SIZE = 640
CONF_THRESHOLD = 0.45
def detect_tokens(frame: np.ndarray) -> list:
    if not MODEL_AVAILABLE or _net is None:
        return hsv_detect_tokens(frame)
    h, w = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(frame, 1/255.0, (INPUT_SIZE, INPUT_SIZE),
                                  swapRB=True, crop=False)
    _net.setInput(blob)
    outputs = _net.forward()
    results = []
    # YOLOv8 output shape: [1, 84, num_anchors] — first 4 are box, rest are class scores
    predictions = outputs.T  # shape: [num_anchors, 84]
    for pred in predictions:
        class_scores = pred[4:]
        class_id = int(np.argmax(class_scores))
        confidence = float(class_scores[class_id])
        if confidence < CONF_THRESHOLD:
            continue
        if class_id not in CLASS_NAMES:
            continue
        # Box is cx, cy, bw, bh normalized to INPUT_SIZE
        cx_norm, cy_norm, bw_norm, bh_norm = pred[:4]
        # Scale back to original frame size
        cx = int(cx_norm * w / INPUT_SIZE)
        cy = int(cy_norm * h / INPUT_SIZE)
        bw = int(bw_norm * w / INPUT_SIZE)
        bh = int(bh_norm * h / INPUT_SIZE)
        x = max(0, cx - bw // 2)
        y = max(0, cy - bh // 2)
        results.append({
            'color': CLASS_NAMES[class_id],
            'cx': cx,
            'cy': cy,
            'x': x,
            'y': y,
            'w': bw,
            'h': bh,
            'area': float(bw * bh),
            'confidence': confidence
        })
    results.sort(key=lambda t: t['area'], reverse=True)
    return results
