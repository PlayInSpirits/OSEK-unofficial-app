"""Recolor the college favicon while preserving its exact emblem."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageOps

root = Path(__file__).resolve().parents[1]
source = Image.open(root / "assets/site-favicon.jpeg").convert("RGB")
source = ImageOps.fit(source, (390, 390), method=Image.Resampling.LANCZOS)
gray = ImageOps.grayscale(source)
alpha = gray.point(lambda value: max(0, min(255, int((244 - value) * 2.3))))
alpha = alpha.filter(ImageFilter.GaussianBlur(0.35))
emblem = Image.new("RGBA", source.size, "#f8f7ff")
emblem.putalpha(alpha)
canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
draw = ImageDraw.Draw(canvas)
draw.rounded_rectangle((0, 0, 511, 511), radius=106, fill="#6251c7")
canvas.alpha_composite(emblem, (61, 61))
canvas.save(root / "assets/icon.png")
canvas.save(root / "web/icon.png")
canvas.save(root / "assets/icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
