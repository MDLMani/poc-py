"""OpenCV QRCodeDetector wrapper — Phase 4 hardened for glare/angle/small codes."""

from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np


_detector: Optional[cv2.QRCodeDetector] = None


def _get_detector() -> cv2.QRCodeDetector:
    global _detector
    if _detector is None:
        _detector = cv2.QRCodeDetector()
    return _detector


def _to_gray(crop_bgr: np.ndarray) -> np.ndarray:
    if crop_bgr.ndim == 3:
        return cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    return crop_bgr


def _ensure_min_size(gray: np.ndarray, min_side: int = 220) -> np.ndarray:
    """Upscale small cells — OpenCV QRCodeDetector likes larger modules."""
    h, w = gray.shape[:2]
    if min(h, w) < min_side:
        scale = max(2, int(np.ceil(min_side / max(1, min(h, w)))))
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    return gray


def _clahe(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _sharpen(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (0, 0), 1.2)
    return cv2.addWeighted(gray, 1.6, blur, -0.6, 0)


def _rotate(gray: np.ndarray, angle_deg: float) -> np.ndarray:
    if abs(angle_deg) < 1e-6:
        return gray
    h, w = gray.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle_deg, 1.0)
    return cv2.warpAffine(
        gray,
        m,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _candidate_views(gray: np.ndarray) -> List[np.ndarray]:
    """Build offline preprocess variants for glare / contrast / small modules."""
    base = _ensure_min_size(gray)
    views: List[np.ndarray] = [base]

    eq = _clahe(base)
    views.append(eq)
    views.append(_sharpen(eq))

    # Global + adaptive thresholds (handles uneven lighting / glare)
    views.append(cv2.threshold(base, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
    views.append(cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
    views.append(
        cv2.adaptiveThreshold(
            eq, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 5
        )
    )
    views.append(
        cv2.adaptiveThreshold(
            eq, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 21, 7
        )
    )

    # Mild morphology to reconnect broken modules under glare
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    morph = cv2.morphologyEx(views[-1], cv2.MORPH_CLOSE, kernel)
    views.append(morph)

    # Extra upscale pass for very small / distant codes
    h, w = base.shape[:2]
    if min(h, w) < 320:
        big = cv2.resize(eq, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
        views.append(big)
        views.append(cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])

    return views


def _try_decode(detector: cv2.QRCodeDetector, img: np.ndarray) -> Optional[str]:
    payload, _points, _ = detector.detectAndDecode(img)
    if payload:
        return payload
    # detectAndDecodeMulti can recover when multiple finder patterns confuse single
    try:
        ok, payloads, _pts, _ = detector.detectAndDecodeMulti(img)
        if ok and payloads:
            for p in payloads:
                if p:
                    return p
    except cv2.error:
        pass
    return None


def decode_qr(crop_bgr: np.ndarray) -> Optional[str]:
    """Decode one QR from a BGR (or grayscale) crop.

    Returns the payload string, or None if nothing readable.
    Phase 4: CLAHE, sharpen, adaptive thresholds, mild rotations, invert,
    and multi-scale upscaling — all offline OpenCV only.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return None

    gray = _to_gray(crop_bgr)
    detector = _get_detector()

    # Small angle sweep for jig misalignment / perspective-ish tilt
    angles = (0.0, -8.0, 8.0, -15.0, 15.0)

    for angle in angles:
        rotated = _rotate(gray, angle) if angle else gray
        for view in _candidate_views(rotated):
            payload = _try_decode(detector, view)
            if payload:
                return payload
            inv = cv2.bitwise_not(view)
            payload = _try_decode(detector, inv)
            if payload:
                return payload

    return None


def decode_qr_with_bbox(
    crop_bgr: np.ndarray,
) -> Tuple[Optional[str], Optional[np.ndarray]]:
    """Decode and also return detector points (or None)."""
    if crop_bgr is None or crop_bgr.size == 0:
        return None, None
    gray = _to_gray(crop_bgr)
    gray = _ensure_min_size(gray)
    detector = _get_detector()
    payload, points, _ = detector.detectAndDecode(gray)
    if payload:
        return payload, points
    # Fall back to full hardened path for payload only
    payload = decode_qr(crop_bgr)
    return payload, None
