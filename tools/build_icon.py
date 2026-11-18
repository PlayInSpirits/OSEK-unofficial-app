"""Package the shared application icon for Windows and the WebView."""

from pathlib import Path

from PIL import Image


root = Path(__file__).resolve().parents[1]
with Image.open(root / "assets/icon.png") as source:
    icon = source.convert("RGBA")
    icon.save(root / "web/icon.png")
    icon.save(
        root / "assets/icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
