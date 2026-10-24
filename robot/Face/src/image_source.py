"""Image acquisition boundary; future cameras can implement the same read contract."""

from pathlib import Path
from typing import Protocol

import cv2
import numpy as np


class ImageSource(Protocol):
    """Return one BGR image array without coupling inference to a file or camera SDK."""

    def read(self, source) -> np.ndarray:
        ...


class FileImageSource:
    """Decode one local image file through OpenCV."""

    def read(self, source: Path | str) -> np.ndarray:
        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"OpenCV could not decode image: {source}")
        return image
