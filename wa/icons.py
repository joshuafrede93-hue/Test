"""Ikon tombol digambar dengan Pillow (tanpa berkas gambar), jadi tombol cukup memakai simbol."""
from PIL import Image, ImageDraw

SCALE = 8  # gambar besar lalu diperkecil agar tepinya halus


def make_icon(name, color="#ffffff", size=64):
    n = size * SCALE
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = n / 24.0  # kanvas 24 x 24
    if name == "play":
        d.polygon([(8 * u, 5 * u), (8 * u, 19 * u), (19.5 * u, 12 * u)], fill=color)
    elif name == "stop":
        d.rounded_rectangle([6 * u, 6 * u, 18 * u, 18 * u], radius=2 * u, fill=color)
    elif name == "mic":
        d.rounded_rectangle([9 * u, 3 * u, 15 * u, 14 * u], radius=3 * u, fill=color)
        d.arc([5.5 * u, 6.5 * u, 18.5 * u, 18.5 * u], 0, 180, fill=color, width=int(1.7 * u))
        d.line([12 * u, 18 * u, 12 * u, 21 * u], fill=color, width=int(1.7 * u))
        d.line([8.5 * u, 21 * u, 15.5 * u, 21 * u], fill=color, width=int(1.7 * u))
    else:
        raise ValueError("ikon tidak dikenal: %s" % name)
    return img.resize((size, size), Image.LANCZOS)
