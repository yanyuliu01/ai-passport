#!/usr/bin/env python3
"""Rebuild the simple moon app icon; Pillow is already a Runtime build dependency."""
from pathlib import Path
from PIL import Image, ImageDraw

assets = Path(__file__).resolve().parents[1] / 'assets'
assets.mkdir(exist_ok=True)
size = 1024
image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((40, 40, 984, 984), radius=220, fill='#e5eff2')
draw.ellipse((237, 224, 791, 778), fill='#639ab0')
draw.ellipse((437, 141, 863, 567), fill='#e5eff2')
draw.polygon([(749, 288), (777, 362), (851, 390), (777, 418), (749, 492), (721, 418), (647, 390), (721, 362)], fill='#639ab0')
image.save(assets / 'icon.png')
image.save(assets / 'icon.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
image.save(assets / 'icon.icns', format='ICNS')
