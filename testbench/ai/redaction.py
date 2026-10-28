"""Redact credentials and personal data before optional provider calls."""

import re
from typing import Any

_PATTERNS = (
    re.compile(
        r"(?i)\b(password|passwd|token|secret|api[_-]?key|access[_-]?key|otp|cvv|card[_-]?number)\s*[:=]\s*[\"']?[^\"'\s,;]+"
    ),
    re.compile(
        r"(?i)\b(?:bearer\s+)?(?:sk-[A-Za-z0-9_-]{12,}|ya29\.[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"
    ),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)"),
    re.compile(r"(?i)(authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n,;]+"),
)
_SENSITIVE_FIELD = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|cookie|authorization|cvv|card)")


def redact_text(value: str) -> tuple[str, int]:
    """Return redacted text and the number of replacements made."""
    count = 0
    result = value
    for pattern in _PATTERNS:
        result, changed = pattern.subn("[REDACTED]", result)
        count += changed
    return result, count


def redact_data(value: Any, *, parent_key: str = "") -> tuple[Any, int]:
    """Recursively redact sensitive mapping values and text."""
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        count = 0
        for key, child in value.items():
            if _SENSITIVE_FIELD.search(str(key)):
                redacted[key] = "[REDACTED]" if child not in (None, "") else child
                count += int(child not in (None, ""))
            else:
                redacted[key], child_count = redact_data(child, parent_key=str(key))
                count += child_count
        return redacted, count
    if isinstance(value, list):
        result = []
        count = 0
        for child in value:
            clean, child_count = redact_data(child, parent_key=parent_key)
            result.append(clean)
            count += child_count
        return result, count
    if isinstance(value, str):
        redacted_text, count = redact_text(value)
        return redacted_text, count
    return value, 0


def redact_image_bytes(image: bytes) -> tuple[bytes, int]:
    """Mask every OCR-detected text region before an image leaves the machine."""
    try:
        import cv2
        import numpy as np
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:
        raise RuntimeError(
            "Safe AI screenshot sharing requires OpenCV and pytesseract. "
            "Install the local Tesseract OCR engine; screenshots are withheld otherwise."
        ) from exc
    decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError("Could not decode screenshot for privacy redaction.")
    try:
        data = pytesseract.image_to_data(decoded, output_type=Output.DICT)
    except Exception as exc:
        raise RuntimeError("Tesseract OCR failed; the screenshot was withheld from the AI provider.") from exc
    count = 0
    for index, text in enumerate(data.get("text", [])):
        if not str(text).strip():
            continue
        try:
            confidence = float(data["conf"][index])
        except (KeyError, TypeError, ValueError):
            confidence = 0
        if confidence < 0:
            continue
        left = max(0, int(data["left"][index]) - 10)
        top = max(0, int(data["top"][index]) - 10)
        right = min(decoded.shape[1], int(data["left"][index]) + int(data["width"][index]) + 10)
        bottom = min(decoded.shape[0], int(data["top"][index]) + int(data["height"][index]) + 10)
        decoded[top:bottom, left:right] = (0, 0, 0)
        count += 1
    success, encoded = cv2.imencode(".png", decoded)
    if not success:
        raise ValueError("Could not encode privacy-redacted screenshot.")
    return encoded.tobytes(), count
