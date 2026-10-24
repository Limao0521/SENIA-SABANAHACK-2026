"""Texturas procedurales del club (mármol, nogal, alfombra, rótulos): imágenes RGB uint8 generadas con numpy, sin
archivos externos. Son deterministas (semilla fija) para que el modelo sea siempre el mismo."""
from __future__ import annotations

from pathlib import Path

import numpy as np

FONTS_SERIF = ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf")
FONTS_SANS = ("/usr/share/fonts/truetype/roboto/unhinted/RobotoCondensed-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _value_noise(h: int, w: int, cells_y: int, cells_x: int, rng: np.random.Generator) -> np.ndarray:
    """Ruido de valor suave en [0, 1]: rejilla aleatoria interpolada (bilineal con suavizado) y periódica."""
    grid = rng.random((cells_y, cells_x))
    y = np.linspace(0, cells_y, h, endpoint=False)
    x = np.linspace(0, cells_x, w, endpoint=False)
    y0, x0 = y.astype(int), x.astype(int)
    fy, fx = y - y0, x - x0
    fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
    y1, x1 = (y0 + 1) % cells_y, (x0 + 1) % cells_x
    a = grid[y0][:, x0] * (1 - fx) + grid[y0][:, x1] * fx
    b = grid[y1][:, x0] * (1 - fx) + grid[y1][:, x1] * fx
    return a * (1 - fy[:, None]) + b * fy[:, None]


def _fbm(h: int, w: int, base: int, octaves: int, rng: np.random.Generator, aspect: float = 1.0) -> np.ndarray:
    out, amp, total = np.zeros((h, w)), 1.0, 0.0
    for o in range(octaves):
        c = base * 2 ** o
        out += amp * _value_noise(h, w, max(1, int(c * aspect)), c, rng)
        total += amp
        amp *= 0.5
    return out / total


def _mix(a, b, t: np.ndarray) -> np.ndarray:
    a, b = np.asarray(a, float), np.asarray(b, float)
    return a + (b - a) * t[..., None]


def marble(size: int = 1024, base=(236, 230, 218), vein=(150, 140, 128), grout=(205, 198, 186),
           grout_px: int = 4, seed: int = 1) -> np.ndarray:
    """Baldosa de mármol crema con vetas grises y junta en el borde (se repite sin cortes)."""
    rng = np.random.default_rng(seed)
    turb = _fbm(size, size, 3, 5, rng)
    u = np.linspace(0, 1, size)
    diag = u[None, :] * 0.8 + u[:, None] * 0.35
    v = np.abs(np.sin(2 * np.pi * (diag * 2.0 + 2.4 * turb)))
    veins = np.clip(1 - v / 0.12, 0, 1) ** 1.2 * 0.6 + np.clip(1 - v / 0.45, 0, 1) * 0.25
    cloud = _fbm(size, size, 6, 4, rng)
    img = _mix(base, vein, veins) * (0.96 + 0.06 * cloud[..., None])
    img[:grout_px, :] = img[-grout_px:, :] = grout
    img[:, :grout_px] = img[:, -grout_px:] = grout
    return np.clip(img, 0, 255).astype(np.uint8)


def walnut(h: int = 1024, w: int = 512, light=(122, 78, 48), dark=(58, 34, 20), seed: int = 2) -> np.ndarray:
    """Chapa de nogal con veta vertical: anillos deformados por ruido y poros finos."""
    rng = np.random.default_rng(seed)
    warp = _fbm(h, w, 3, 4, rng, aspect=1.5)
    x = np.linspace(0, 1, w)[None, :]
    rings = 0.5 + 0.5 * np.sin(2 * np.pi * (x * 7 + 0.9 * warp))
    pores = _value_noise(h, w, 400, 90, rng)
    t = np.clip(0.65 * rings ** 2 + 0.25 * warp + 0.15 * pores, 0, 1)
    return np.clip(_mix(light, dark, t), 0, 255).astype(np.uint8)


def rug(h: int = 512, w: int = 768, field=(116, 26, 32), border=(196, 160, 92), inner=(48, 24, 22),
        seed: int = 3) -> np.ndarray:
    """Alfombra burdeos con cenefa dorada y un rombo central."""
    rng = np.random.default_rng(seed)
    img = np.empty((h, w, 3))
    img[:] = field
    yy, xx = np.mgrid[0:h, 0:w]
    edge = np.minimum.reduce([yy, xx, h - 1 - yy, w - 1 - xx])
    img[(edge >= 18) & (edge < 30)] = border
    img[(edge >= 36) & (edge < 40)] = border
    img[edge < 18] = inner
    cy, cx = h / 2, w / 2
    diamond = np.abs(yy - cy) / (h * 0.28) + np.abs(xx - cx) / (w * 0.28)
    img[(diamond < 1.0) & (diamond > 0.93)] = border
    img[diamond < 0.35] = border
    img *= (0.9 + 0.12 * _value_noise(h, w, 120, 180, rng))[..., None]
    return np.clip(img, 0, 255).astype(np.uint8)


def _font(paths: tuple, size: int):
    from PIL import ImageFont
    path = next((p for p in paths if Path(p).exists()), None)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


def text_sign(text: str, w: int, h: int, fg=(214, 178, 96), bg=(46, 28, 18), serif: bool = True,
              tracking: float = 0.0, fill: float = 0.62) -> np.ndarray:
    """Rótulo de una línea centrado que cabe en la imagen (letras de latón sobre nogal oscuro por defecto).
    ``tracking``: espacio extra entre letras, en fracciones del alto de letra."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), tuple(bg))
    draw = ImageDraw.Draw(img)
    paths = FONTS_SERIF if serif else FONTS_SANS
    size = int(h * fill)
    while True:
        font = _font(paths, size)
        widths = [draw.textlength(c, font=font) for c in text]
        total = sum(widths) + tracking * size * (len(text) - 1)
        if total <= 0.88 * w or size < 8:
            break
        size = int(size * 0.92)
    box = draw.textbbox((0, 0), text, font=font)
    x, y = (w - total) / 2, (h - (box[3] - box[1])) / 2 - box[1]
    for c, cw in zip(text, widths):
        draw.text((x, y), c, font=font, fill=tuple(fg))
        x += cw + tracking * size
    return np.asarray(img)


def painting(w: int = 512, h: int = 384, seed: int = 4) -> np.ndarray:
    """Cuadro abstracto de tonos tierra para las paredes (manchas suaves de color)."""
    rng = np.random.default_rng(seed)
    palette = np.array([(196, 150, 96), (120, 72, 48), (60, 76, 70), (224, 204, 168), (150, 52, 44)], float)
    n = [_fbm(h, w, 2, 3, rng) for _ in range(3)]
    t = np.clip((n[0] * 0.6 + n[1] * 0.4) * len(palette) - 1, 0, len(palette) - 1.001)
    i = t.astype(int)
    img = palette[i] * (1 - (t - i))[..., None] + palette[np.minimum(i + 1, len(palette) - 1)] * (t - i)[..., None]
    img *= (0.85 + 0.25 * n[2])[..., None]
    return np.clip(img, 0, 255).astype(np.uint8)


def directory(title: str, rows, w: int, h: int, fg=(214, 178, 96), bg=(30, 20, 14)) -> np.ndarray:
    """Directorio de pisos: título y filas (número, nombre) en latón sobre nogal oscuro."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), tuple(bg))
    draw = ImageDraw.Draw(img)
    title_font = _font(FONTS_SERIF, int(h * 0.09))
    box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((w - (box[2] - box[0])) / 2 - box[0], int(h * 0.06)), title, font=title_font, fill=tuple(fg))
    y_line = int(h * 0.2)
    draw.line([(int(w * 0.12), y_line), (int(w * 0.88), y_line)], fill=tuple(fg), width=max(2, h // 200))
    size = int(h * 0.06)
    font = _font(FONTS_SANS, size)
    while size > 10 and max((draw.textlength(name, font=font) for _, name in rows), default=0) > w * 0.66:
        size -= 1                              # nombres largos: letra más pequeña para que quepan
        font = _font(FONTS_SANS, size)
    step = (h * 0.72) / max(len(rows), 1)
    for i, (num, name) in enumerate(rows):
        y = int(h * 0.26 + i * step)
        draw.text((int(w * 0.12), y), num, font=font, fill=tuple(fg))
        draw.text((int(w * 0.28), y), name, font=font, fill=(236, 226, 206))
    return np.asarray(img)


def plaque(title: str, motto: str, lines, w: int, h: int, fg=(214, 178, 96), bg=(30, 20, 14)) -> np.ndarray:
    """Placa de latón sobre nogal: título, lema en cursiva y una línea por valor."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), tuple(bg))
    draw = ImageDraw.Draw(img)
    draw.rectangle([int(w * 0.03), int(h * 0.04), int(w * 0.97), int(h * 0.96)], outline=tuple(fg),
                   width=max(2, h // 160))

    def centered(text, font, y, fill):
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((w - (box[2] - box[0])) / 2 - box[0], y), text, font=font, fill=fill)

    centered(title, _font(FONTS_SERIF, int(h * 0.085)), int(h * 0.1), tuple(fg))
    centered(motto, _font(FONTS_SERIF, int(h * 0.055)), int(h * 0.25), (236, 226, 206))
    y_line = int(h * 0.35)
    draw.line([(int(w * 0.2), y_line), (int(w * 0.8), y_line)], fill=tuple(fg), width=max(2, h // 240))
    font = _font(FONTS_SANS, int(h * 0.06))
    step = (h * 0.52) / max(len(lines), 1)
    for i, text in enumerate(lines):
        centered(text, font, int(h * 0.40 + i * step), (236, 226, 206))
    return np.asarray(img)

