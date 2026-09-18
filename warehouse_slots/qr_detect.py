"""OpenCV QRCodeDetector wrapper for a single crop."""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np


_detector: Optional[cv2.QRCodeDetector] = None


def _get_detector() -> cv2.QRCodeDetector:
    global _detector
    if _detector is None:
        _detector = cv2.QRCodeDetector()
    return _detector


def decode_qr(crop_bgr: np.ndarray) -> Optional[str]:
    """Decode one QR from a BGR (or grayscale) crop.

    Returns the payload string, or None if nothing readable.
    Tries the raw crop first, then a few lightweight offline
    preprocessings to improve reliability on high-contrast fixtures.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return None

    if crop_bgr.ndim == 3:
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop_bgr

    # Upscale small cells — OpenCV QRCodeDetector likes larger modules.
    h, w = gray.shape[:2]
    if min(h, w) < 200:
        scale = max(2, int(np.ceil(200 / min(h, w))))
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    candidates = [
        gray,
        cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 5
        ),
    ]

    detector = _get_detector()
    for img in candidates:
        payload, points, _ = detector.detectAndDecode(img)
        if payload:
            return payload
        # Also try inverted (white-on-black vs black-on-white).
        inv = cv2.bitwise_not(img)
        payload, points, _ = detector.detectAndDecode(inv)
        if payload:
            return payload

    return None


def decode_qr_with_bbox(
    crop_bgr: np.ndarray,
) -> Tuple[Optional[str], Optional[np.ndarray]]:
    """Decode and also return detector points (or None)."""
    if crop_bgr is None or crop_bgr.size == 0:
        return None, None
    if crop_bgr.ndim == 3:
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop_bgr
    detector = _get_detector()
    payload, points, _ = detector.detectAndDecode(gray)
    if payload:
        return payload, points
    return None, None
