# -*- coding: utf-8 -*-
"""Statek - z chroma atlasu vyrobi samostatne WebP sprity pro scenu.

Vstup:  design/statek-art/statek-atlas-c-key.png (1536x1024, zeleny klic, 3 sloupce x 6 rad)
Vystup: web/img/farm/animals/{art}-{stav}.webp + dog.webp + fox.webp

Proc samostatne soubory a ne orez z atlasu: v atlasu se zvirata misty dotykaji,
takze obdelnikovy orez tahal do policka kus souseda (krava ve stavu ready mela
pod sebou kus konske hrivy). Kazdy sprite se maskuje vlastni komponentou, cizi
pixely se odalfuji a projevit se nemuzou.

Spusteni: python design/statek-art/split_atlas.py
"""
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).parent / "statek-atlas-c-key.png"
OUT = ROOT / "web" / "img" / "farm" / "animals"

ROWS = ["chicken", "goat", "sheep", "cow", "horse", "special"]
STATES = ["hungry", "producing", "ready"]

KEY_LO, KEY_HI = 40, 90   # zelenost (g - max(r,b)): pod LO kryje, nad HI je pozadi
MIN_PX = 200              # mensi skvrny = smeti z klicovani

# Konska hriva se v atlasu dotyka kravskeho kopyta -> jedna souvisla skvrna.
# Rez vede po viditelne hrane mezi nimi, obe zvirata zustanou cela.
SEAM = ((1011, 634), (1025, 642))


def unkey(path):
    """Zeleny klic -> RGBA. Mekky prechod na hranach + odstraneni zeleneho lemu."""
    p = np.array(Image.open(path).convert("RGB")).astype(np.int16)
    r, g, b = p[:, :, 0], p[:, :, 1], p[:, :, 2]
    mx = np.maximum(r, b)

    a = np.clip((KEY_HI - (g - mx)) / (KEY_HI - KEY_LO), 0, 1)
    p[:, :, 1] = np.minimum(g, mx)   # despill: zelenou nad uroven r/b srazit dolu
    return np.dstack([p, (a * 255).round()]).astype(np.uint8)


def label(mask):
    """Scanline union-find CC (4-souvislost)."""
    h, w = mask.shape
    parent = [0]
    lab = np.zeros((h, w), dtype=np.int32)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for y in range(h):
        for x in np.where(mask[y])[0]:
            up = lab[y - 1, x] if y else 0
            left = lab[y, x - 1] if x else 0
            if up and left:
                lab[y, x] = min(up, left)
                ra, rb = find(up), find(left)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
            elif up or left:
                lab[y, x] = up or left
            else:
                lab[y, x] = len(parent)
                parent.append(len(parent))

    flat = lab.ravel()
    nz = flat > 0
    flat[nz] = np.array([find(i) for i in range(len(parent))])[flat[nz]]
    return flat.reshape(h, w)


def cut_seam(mask):
    (x0, y0), (x1, y1) = SEAM
    n = max(abs(x1 - x0), abs(y1 - y0)) * 4
    for i in range(n + 1):
        x = round(x0 + (x1 - x0) * i / n)
        y = round(y0 + (y1 - y0) * i / n)
        mask[y - 1:y + 2, x] = False   # 3px siroky, at rez drzi i pres antialias


def col_cuts(mask):
    """2 nejsirsi ciste svisle mezery = hranice sloupcu."""
    proj = mask.sum(axis=0)
    gaps, s = [], None
    for i, v in enumerate(proj):
        if v == 0 and s is None:
            s = i
        if v != 0 and s is not None:
            gaps.append((s, i))
            s = None
    gaps = [g for g in gaps if g[0] > 0]
    return sorted((a + b) // 2 for a, b in sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:2])


def main():
    px = unkey(SRC)
    H, W = px.shape[:2]

    mask = px[:, :, 3] > 16
    cut_seam(mask)
    lab = label(mask)

    cx = [0] + col_cuts(mask) + [W]
    print(f"hranice sloupcu: {cx[1:3]}")

    cells = {}
    for i in np.unique(lab[lab > 0]):
        ys, xs = np.where(lab == i)
        if len(ys) < MIN_PX:
            continue
        # radu urci teziste v pravidelne mrizce 6 rad, sloupec ciste svisle mezery
        r = min(int((ys.min() + ys.max()) / 2 // (H / 6)), 5)
        c = next(j for j in range(3) if cx[j] <= (xs.min() + xs.max()) / 2 < cx[j + 1])
        cells.setdefault((r, c), []).append(i)

    OUT.mkdir(parents=True, exist_ok=True)
    for (r, c), ids in sorted(cells.items()):
        name = f"{ROWS[r]}-{STATES[c]}" if ROWS[r] != "special" else ("dog" if c == 0 else "fox")
        keep = np.isin(lab, ids)              # jen vlastni komponenty (zvire + jeho produkt)
        ys, xs = np.where(keep)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        out = px[y0:y1, x0:x1].copy()
        out[:, :, 3] *= keep[y0:y1, x0:x1]    # cizi pixely v obdelniku odalfovat
        Image.fromarray(out).save(OUT / f"{name}.webp", quality=92, method=6)
        print(f"  {name:18s} {x1 - x0:3d}x{y1 - y0:3d}  {len(ids)} kompon.")

    want = {f"{a}-{s}" for a in ROWS[:5] for s in STATES} | {"dog", "fox"}
    got = {p.stem for p in OUT.glob("*.webp")}
    assert got == want, f"chybi/prebyva: {want ^ got}"
    print(f"OK {len(want)} spritu -> {OUT}")


if __name__ == "__main__":
    main()
