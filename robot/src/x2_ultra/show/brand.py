"""Marcas del escenario: logos de Autoriente, Ansrobex y la Universidad de La Sabana (``assets/brand/``) y sus colores
oficiales. El de La Sabana (club del reto 2) es el de Wikimedia Commons, aplanado sobre blanco.

Los colores salen de los propios logos. Los logos se escalan aquí a la resolución de cada uso: los rótulos del
frontal de la tarima (logo sobre su fondo de marca) y la imagen de la pantalla cuando no hay vídeo. Pillow ya viene
con imageio.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from x2_ultra import paths

BRAND_DIR = paths.ASSETS_DIR / "brand"

# Colores oficiales (RGB 0-255), muestreados de los logos.
AUTORIENTE_NAVY = (20, 30, 60)          # fondo del logo
AUTORIENTE_BLUE = (40, 96, 152)         # lazo y letras
AUTORIENTE_GOLD = (216, 168, 48)        # lazo
ANSROBEX_ORANGE = (248, 104, 0)
ANSROBEX_CYAN = (0, 208, 248)
ANSROBEX_GRAY = (90, 90, 90)            # letras
UNISABANA_BLUE = (5, 54, 115)
UNISABANA_GOLD = (209, 161, 13)
WHITE = (255, 255, 255)

LOGOS = {"autoriente": ("autoriente.png", AUTORIENTE_NAVY), "ansrobex": ("ansrobex.png", WHITE),
         "unisabana": ("unisabana.png", WHITE)}
FONTS = ("/usr/share/fonts/truetype/roboto/unhinted/RobotoCondensed-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def rgba01(rgb, alpha: float = 1.0) -> tuple[float, float, float, float]:
    return (rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, alpha)


def _logo(name: str):
    from PIL import Image
    return Image.open(BRAND_DIR / LOGOS[name][0]).convert("RGB")


def _keyed(name: str):
    """Logo recortado y con su fondo transparente: el alfa crece con la distancia al color de fondo, interpolado
    entre las esquinas (el fondo del logo de Autoriente es un degradado)."""
    from PIL import Image
    a = np.asarray(_logo(name)).astype(float)
    h, w, _ = a.shape
    u = np.linspace(0, 1, w)[None, :, None]
    v = np.linspace(0, 1, h)[:, None, None]
    c00, c01, c10, c11 = a[0, 0], a[0, -1], a[-1, 0], a[-1, -1]
    bg = (1 - v) * ((1 - u) * c00 + u * c01) + v * ((1 - u) * c10 + u * c11)
    alpha = np.clip((np.linalg.norm(a - bg, axis=2) - 12) / 40, 0, 1)
    rgba = np.dstack([a, 255 * alpha]).astype(np.uint8)
    ys, xs = np.nonzero(alpha > 0.1)                  # sin el margen que trae la imagen
    return Image.fromarray(rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1], "RGBA")


def _fit(img, max_w: int, max_h: int):
    from PIL import Image
    s = min(max_w / img.width, max_h / img.height)
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


def sign_image(name: str, width: int, height: int, background=None, fill: float = 0.8) -> np.ndarray:
    """Rótulo: el logo centrado (RGB, alto × ancho × 3). Sobre ``background`` (por defecto el fondo del propio logo);
    si el fondo del logo es otro (Ansrobex, blanco, sobre fondo oscuro), el logo va en una placa de su color."""
    from PIL import Image, ImageDraw
    logo_bg = LOGOS[name][1]
    background = tuple(background or logo_bg)
    img = Image.new("RGB", (width, height), background)
    plate = background != tuple(logo_bg)
    logo = _fit(_keyed(name), int(width * 0.9 * (0.85 if plate else 1)), int(height * fill * (0.8 if plate else 1)))
    x, y = (width - logo.width) // 2, (height - logo.height) // 2
    if plate:
        pad = int(0.18 * logo.height)
        ImageDraw.Draw(img).rounded_rectangle([x - pad, y - pad, x + logo.width + pad, y + logo.height + pad],
                                              radius=pad, fill=logo_bg)
    img.paste(logo, (x, y), logo)
    return np.asarray(img)


def title_card(width: int, height: int, subtitle: str = "Hackathon Challenge 1",
               note: str = "X2 brand presentation and two-robot performance") -> np.ndarray:
    """Imagen de la pantalla sin vídeo: fondo de Autoriente, su logo y el de Ansrobex en una placa blanca."""
    from PIL import Image, ImageDraw, ImageFont
    v = np.linspace(0, 1, height)[:, None, None]
    top, bottom = np.array(AUTORIENTE_NAVY, float), np.array((30, 62, 110), float)
    grad = ((1 - v) * top + v * bottom) * np.ones((1, width, 1))
    img = Image.fromarray(grad.astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)
    # Franja con los colores de las dos marcas.
    bar = max(4, height // 90)
    stripes = (AUTORIENTE_BLUE, AUTORIENTE_GOLD, ANSROBEX_ORANGE, ANSROBEX_CYAN)
    for i, c in enumerate(stripes):
        draw.rectangle([i * width // 4, height - bar, (i + 1) * width // 4, height], fill=c)

    auto = _fit(_keyed("autoriente"), int(width * 0.62), int(height * 0.36))
    img.paste(auto, ((width - auto.width) // 2, int(height * 0.10)), auto)
    plate_h = int(height * 0.20)
    ans = _fit(_keyed("ansrobex"), int(width * 0.3), int(plate_h * 0.82))
    plate_w = ans.width + plate_h // 2
    px, py = (width - plate_w) // 2, int(height * 0.52)
    draw.rounded_rectangle([px, py, px + plate_w, py + plate_h], radius=plate_h // 6, fill=WHITE)
    img.paste(ans, (px + (plate_w - ans.width) // 2, py + (plate_h - ans.height) // 2), ans)

    font_path = next((f for f in FONTS if Path(f).exists()), None)
    for text, y, size, color in ((subtitle, 0.77, 0.055, WHITE), (note, 0.85, 0.036, (190, 210, 240))):
        px_size = int(height * size)
        font = ImageFont.truetype(font_path, px_size) if font_path else ImageFont.load_default(px_size)
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (box[2] - box[0])) / 2 - box[0], int(height * y)), text, font=font, fill=color)
    return np.asarray(img)
