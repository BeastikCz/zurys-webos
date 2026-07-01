"""Vygeneruje PNG graf ekonomiky 21.6-29.6 (Discord-ready, dark theme). Pillow."""
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 720
BG = (22, 19, 13)
CARD = (36, 29, 18)
TXT = (240, 233, 221)
SEC = (155, 145, 127)
GRID = (58, 51, 38)
GREEN = (40, 196, 140)
RED = (227, 73, 72)

def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()

F_TITLE = font("C:/Windows/Fonts/arialbd.ttf", 34)
F_VAL = font("C:/Windows/Fonts/arialbd.ttf", 30)
F_LBL = font("C:/Windows/Fonts/arial.ttf", 17)
F_AX = font("C:/Windows/Fonts/arial.ttf", 16)
F_LEG = font("C:/Windows/Fonts/arial.ttf", 18)
F_FOOT = font("C:/Windows/Fonts/arial.ttf", 14)

def fmt(n):
    s = f"{abs(int(n)):,}".replace(",", " ")
    return ("−" if n < 0 else "") + s

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

def ctext(x, y, s, f, fill, anchor="la"):
    d.text((x, y), s, font=f, fill=fill, anchor=anchor)

ctext(40, 28, "Ekonomika sedláků", F_TITLE, TXT)
ctext(42, 70, "21. 6. 00:00 → 29. 6. 12:00 UTC", F_LBL, SEC)

# KPI tiles
tiles = [("Vydělano", 10340298, GREEN),
         ("Proděláno / utraceno", -10468338, RED),
         ("Net oběh", -128040, TXT),
         ("Gambling net (hráči)", -740099, RED)]
tx, ty, tw, th, gap = 40, 105, 268, 86, 16
for i, (lbl, val, col) in enumerate(tiles):
    x = tx + i * (tw + gap)
    d.rounded_rectangle([x, ty, x + tw, ty + th], radius=12, fill=CARD)
    ctext(x + 18, ty + 14, lbl, F_LBL, SEC)
    shown = fmt(val) if lbl != "Proděláno / utraceno" else fmt(10468338)
    ctext(x + 18, ty + 40, shown, F_VAL, col)

# chart
labels = ["21.6", "22.6", "23.6", "24.6", "25.6", "26.6", "27.6", "28.6", "29.6*"]
vyd = [3023441, 1007814, 1305644, 864369, 1549388, 815305, 659592, 826213, 288532]
pro = [3403899, 1039889, 1429644, 708051, 1431415, 856444, 632947, 669551, 296498]
cl, cr, ctop, cb = 105, 1160, 250, 600
ch = cb - ctop
cw = cr - cl
maxv = 3_500_000
# gridlines + y labels
for gv in (0, 1_000_000, 2_000_000, 3_000_000):
    y = cb - gv / maxv * ch
    d.line([cl, y, cr, y], fill=GRID, width=1)
    ctext(cl - 12, y, f"{gv // 1_000_000} M" if gv else "0", F_AX, SEC, anchor="rm")
groupw = cw / len(labels)
barw, innergap = 34, 6
for i in range(len(labels)):
    gx = cl + i * groupw
    sx = gx + (groupw - (2 * barw + innergap)) / 2
    for j, (data, col) in enumerate(((vyd, GREEN), (pro, RED))):
        bx = sx + j * (barw + innergap)
        bh = data[i] / maxv * ch
        d.rounded_rectangle([bx, cb - bh, bx + barw, cb], radius=4, fill=col)
    ctext(gx + groupw / 2, cb + 10, labels[i], F_AX, SEC, anchor="ma")

# legend
ly = 645
d.rounded_rectangle([cl, ly, cl + 16, ly + 16], radius=3, fill=GREEN)
ctext(cl + 24, ly - 1, "Vydělano (change > 0)", F_LEG, TXT)
lx2 = cl + 300
d.rounded_rectangle([lx2, ly, lx2 + 16, ly + 16], radius=3, fill=RED)
ctext(lx2 + 24, ly - 1, "Proděláno / utraceno (change < 0)", F_LEG, TXT)

ctext(40, 686, "Net oběh −128 040 = mírná deflace (zdravé). 29.6 jen do poledne. Zdroj: points_log produkce.",
      F_FOOT, SEC)

out = "deliverables/ekonomika_21-29_6.png"
img.save(out)
print("PNG uloženo:", out, img.size)
