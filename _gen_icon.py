"""One-off generator for the Data Annotator app icon.

Produces icon.ico (multi-resolution) and icon.png. Run once; the artefacts
are committed alongside main.py.
"""
from PIL import Image, ImageDraw

BG_TOP = (38, 38, 48)
BG_BOT = (24, 24, 30)
FRAME = (90, 95, 110)
ACCENT = (59, 130, 246)
DOTS = [
    ((0.30, 0.32), (34, 197, 94)),     # normal -> green
    ((0.62, 0.30), (239, 68, 68)),     # cracked -> red
    ((0.38, 0.62), (249, 115, 22)),    # bent lead -> orange
    ((0.70, 0.66), (168, 85, 247)),    # contaminated -> purple
    ((0.50, 0.46), (250, 204, 21)),    # scratched -> yellow (anchor)
]


def render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded background card
    pad = max(1, size // 16)
    r = max(2, size // 8)
    card = (pad, pad, size - pad, size - pad)
    # gradient bg by painting horizontal strips
    for y in range(card[1], card[3]):
        t = (y - card[1]) / max(1, card[3] - card[1])
        col = tuple(int(BG_TOP[i] * (1 - t) + BG_BOT[i] * t) for i in range(3))
        d.line([(card[0], y), (card[2], y)], fill=col + (255,))
    # mask to rounded rect
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(card, radius=r, fill=255)
    rounded = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rounded.paste(img, (0, 0), mask)
    img = rounded
    d = ImageDraw.Draw(img)

    # frame border
    border_w = max(1, size // 32)
    d.rounded_rectangle(card, radius=r, outline=FRAME + (255,), width=border_w)

    # grid hint (subtle)
    grid_col = (255, 255, 255, 18)
    inner = (card[0] + border_w * 2, card[1] + border_w * 2,
             card[2] - border_w * 2, card[3] - border_w * 2)
    iw = inner[2] - inner[0]
    ih = inner[3] - inner[1]
    for gx in (1, 2):
        x = inner[0] + iw * gx / 3
        d.line([(x, inner[1]), (x, inner[3])], fill=grid_col, width=1)
    for gy in (1, 2):
        y = inner[1] + ih * gy / 3
        d.line([(inner[0], y), (inner[2], y)], fill=grid_col, width=1)

    # colored attribute dots
    dot_r = max(2, size // 12)
    ring_w = max(1, size // 48)
    for (fx, fy), color in DOTS[:-1]:
        cx = inner[0] + iw * fx
        cy = inner[1] + ih * fy
        d.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r),
                  fill=color + (255,),
                  outline=(255, 255, 255, 230), width=ring_w)

    # anchor point with crosshair (the "active" annotation)
    (ax, ay), acolor = DOTS[-1]
    cx = inner[0] + iw * ax
    cy = inner[1] + ih * ay
    big_r = int(dot_r * 1.45)
    # outer ring
    d.ellipse((cx - big_r - ring_w * 2, cy - big_r - ring_w * 2,
               cx + big_r + ring_w * 2, cy + big_r + ring_w * 2),
              outline=(255, 255, 255, 235), width=max(1, ring_w + 1))
    d.ellipse((cx - big_r, cy - big_r, cx + big_r, cy + big_r),
              fill=acolor + (255,),
              outline=(255, 255, 255, 255), width=ring_w + 1)
    # crosshair through anchor extending to frame
    cross_col = (255, 255, 255, 200)
    cross_w = max(1, size // 40)
    d.line([(inner[0] + border_w, cy), (cx - big_r - ring_w, cy)],
           fill=cross_col, width=cross_w)
    d.line([(cx + big_r + ring_w, cy), (inner[2] - border_w, cy)],
           fill=cross_col, width=cross_w)
    d.line([(cx, inner[1] + border_w), (cx, cy - big_r - ring_w)],
           fill=cross_col, width=cross_w)
    d.line([(cx, cy + big_r + ring_w), (cx, inner[3] - border_w)],
           fill=cross_col, width=cross_w)
    # small inner crosshair
    inner_l = max(1, dot_r // 2)
    d.line([(cx - inner_l, cy), (cx + inner_l, cy)],
           fill=(255, 255, 255, 255), width=max(1, cross_w))
    d.line([(cx, cy - inner_l), (cx, cy + inner_l)],
           fill=(255, 255, 255, 255), width=max(1, cross_w))

    return img


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [render(s) for s in sizes]
    images[-1].save("icon.png")
    images[-1].save(
        "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )
    print("Wrote icon.ico and icon.png")


if __name__ == "__main__":
    main()
