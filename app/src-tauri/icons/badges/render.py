"""The menu bar's red badges, drawn once and committed: `uv run --with pillow python render.py`.
macOS scales a menu bar image to 18 points tall, so the canvas is that at 4x, with the badge a little inside it."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RED, TALL, INSET, K = '#C8432B', 72, 6, 4  # --quiet-needs-you
for text in [*map(str, range(1, 10)), '9+']:
    wide = TALL if len(text) == 1 else 104
    im = Image.new('RGBA', (wide * K, TALL * K), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([INSET * K, INSET * K, (wide - INSET) * K - 1, (TALL - INSET) * K - 1],
                        radius=(TALL - 2 * INSET) * K // 2, fill=RED)
    font = ImageFont.truetype('/System/Library/Fonts/SFNS.ttf', 44 * K)
    font.set_variation_by_axes([700, 44, 0][:len(font.get_variation_axes())])
    d.text((wide * K / 2, TALL * K / 2), text, font=font, fill='white', anchor='mm')
    name = 'more' if text == '9+' else text
    im.resize((wide, TALL), Image.LANCZOS).save(Path(__file__).parent / f'{name}.png')
