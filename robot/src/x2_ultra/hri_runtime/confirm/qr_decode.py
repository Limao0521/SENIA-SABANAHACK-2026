"""Decodificación de QR: zxing-cpp si importable, si no OpenCV QRCodeDetector.

La imagen se carga con PIL/numpy (disponibles en el venv principal) para no
depender de cv2 más que como respaldo del decodificador. Sin ningún
decodificador, ``decode_qr_texts`` devuelve ``("decoder_unavailable", [])``.
"""
from __future__ import annotations

from pathlib import Path

QR_TEXT_PREFIXES = ("QR01.", "QC1:", "IDV1.", "IDV2.")


def _load_gray(image):
    """str/Path/bytes/PIL/numpy -> ndarray gris uint8 (o None)."""
    import numpy as np

    if isinstance(image, (str, Path)):
        p = Path(image)
        if p.is_file():
            image = p.read_bytes()
        else:
            return None
    if isinstance(image, bytes):
        try:
            import io

            from PIL import Image
            image = Image.open(io.BytesIO(image))
        except Exception:
            return None
    if hasattr(image, "convert"):  # PIL Image
        image = np.asarray(image.convert("L"))
    arr = np.asarray(image)
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    return arr.astype(np.uint8) if arr.dtype != np.uint8 else arr


def decode_qr_texts(qr_image) -> tuple[str | None, list[str]]:
    """-> (error, texts). error None si ok; 'decoder_unavailable'/'unreadable'."""
    if isinstance(qr_image, str) and qr_image.startswith(QR_TEXT_PREFIXES) and "\n" not in qr_image:
        return None, [qr_image]  # texto QR ya decodificado (p. ej. por T1)
    gray = _load_gray(qr_image)
    if gray is None:
        if isinstance(qr_image, (bytes, bytearray)):
            try:
                text = bytes(qr_image).decode("utf-8").strip()
                if text.startswith(QR_TEXT_PREFIXES):
                    return None, [text]
            except Exception:
                pass
        return "unreadable", []
    texts, any_decoder = [], False
    for img in _variants(gray):
        found, ok = _decode_once(img)
        any_decoder |= ok
        if found:
            return None, found
    return (None, texts) if any_decoder else ("decoder_unavailable", [])


def _variants(gray):
    """Imagen completa y, para un QR pequeño en la cámara gran angular del X2 (1280x720),
    recortes centrales ampliados y una versión con contraste ecualizado."""
    yield gray
    try:
        import cv2
    except ImportError:
        return
    h, w = gray.shape[:2]
    for frac, scale in ((0.6, 2.0), (0.4, 3.0)):
        ch, cw = int(h * frac), int(w * frac)
        y0, x0 = (h - ch) // 2, (w - cw) // 2
        crop = gray[y0:y0 + ch, x0:x0 + cw]
        yield cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    yield cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)


def _decode_once(img) -> tuple[list[str], bool]:
    """-> (textos, hubo_decodificador). zxing-cpp primero; OpenCV (Aruco y clásico) de respaldo."""
    ok_any = False
    try:
        import zxingcpp
        ok_any = True
        res = zxingcpp.read_barcodes(img, formats=zxingcpp.BarcodeFormat.QRCode)
        texts = [r.text for r in res or [] if r.text]
        if texts:
            return texts, True
    except ImportError:
        pass
    except Exception:
        pass
    try:
        import cv2
    except ImportError:
        return [], ok_any
    detectors = [cv2.QRCodeDetector()]
    if hasattr(cv2, "QRCodeDetectorAruco"):
        detectors.insert(0, cv2.QRCodeDetectorAruco())
    for det in detectors:
        try:
            ok, texts, _pts, _ = det.detectAndDecodeMulti(img)
            texts = [t for t in (texts or []) if t] if ok else []
            if texts:
                return texts, True
        except Exception:
            continue
    return [], True
