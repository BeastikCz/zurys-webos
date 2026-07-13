# -*- coding: utf-8 -*-
"""Style board: 4 stylove smery x 3 stavy slepice pro Statek. Vystup: SVG."""

CARD_W, CARD_H, GAP = 360, 300, 20
MARGIN = 80
W = MARGIN * 2 + CARD_W * 3 + GAP * 2          # 1280
ROW_LABEL, ROW_GAP = 52, 38
TITLE_H = 130
ROWS = 4
H = TITLE_H + ROWS * (ROW_LABEL + CARD_H + 46 + ROW_GAP) + 30

INK = "#f6eedc"
DIM = "#9e8a69"
GOLD = "#ffd75e"
CREAM = "#fff3e0"
SHADE = "#ecd3ac"
RED = "#ff6b6b"
BEAK = "#ffb340"
LEG = "#d98a2b"
OUT = "#241505"


def soft_chicken(state):
    """B: mekka cozy ilustrace (gradient, bez obrysu)."""
    p = []
    legs = state != "hungry"
    wrap_open = ""
    wrap_close = ""
    if state == "hungry":
        wrap_open = '<g transform="translate(0,20) scale(1,0.92)" filter="url(#dim)">'
        wrap_close = "</g>"
    elif state == "ready":
        wrap_open = '<g transform="rotate(-3 180 170)">'
        wrap_close = "</g>"
    p.append(wrap_open)
    # ocas
    for (cx, cy, rx, ry, rot) in [(104, 128, 27, 16, -32), (96, 146, 25, 14, -8), (101, 162, 22, 12, 14)]:
        p.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="#e8c9a0" transform="rotate({rot} {cx} {cy})"/>')
    # telo + krk + hlava
    p.append('<ellipse cx="170" cy="165" rx="78" ry="60" fill="url(#softBody)"/>')
    p.append('<ellipse cx="213" cy="125" rx="42" ry="48" fill="url(#softBody)"/>')
    p.append('<circle cx="238" cy="95" r="36" fill="url(#softBody)"/>')
    # hrebinek (u hladove svesene)
    comb = '<g fill="#ff6b6b">' \
           '<circle cx="222" cy="60" r="11"/><circle cx="238" cy="52" r="12"/><circle cx="254" cy="60" r="11"/></g>'
    if state == "hungry":
        comb = f'<g transform="rotate(14 238 62)">{comb}</g>'
    p.append(comb)
    # zobak + lalok
    p.append('<polygon points="270,88 297,97 270,107" fill="#ffb340"/>')
    p.append('<polygon points="270,97 297,97 270,107" fill="#e8931f"/>')
    p.append('<ellipse cx="261" cy="118" rx="8" ry="12" fill="#ff6b6b"/>')
    # oko
    if state == "hungry":
        p.append('<path d="M240 92 q7 5 14 0" stroke="#33220f" stroke-width="3.5" fill="none" stroke-linecap="round"/>')
    elif state == "ready":
        p.append('<path d="M240 88 q7 -7 14 0" stroke="#33220f" stroke-width="3.5" fill="none" stroke-linecap="round"/>')
    else:
        p.append('<circle cx="247" cy="90" r="5.2" fill="#33220f"/><circle cx="249" cy="88" r="1.8" fill="#fff"/>')
    # kridlo
    p.append('<ellipse cx="152" cy="170" rx="42" ry="30" fill="#f0d2ab" transform="rotate(-14 152 170)"/>')
    p.append('<path d="M120 178 q30 16 62 6 M126 190 q26 12 50 4" stroke="#d9b586" stroke-width="3" fill="none" stroke-linecap="round"/>')
    # nohy
    if legs:
        p.append('<g stroke="#d98a2b" stroke-width="5" stroke-linecap="round">'
                 '<path d="M150 222 L148 250 M140 250 L158 250"/>'
                 '<path d="M192 222 L194 250 M184 250 L202 250"/></g>')
    p.append(wrap_close)
    if state == "hungry":
        p.append(f'<g fill="{DIM}" font-family="Segoe UI" font-weight="600">'
                 '<text x="285" y="70" font-size="26">z</text>'
                 '<text x="302" y="52" font-size="20">z</text>'
                 '<text x="315" y="38" font-size="15">z</text></g>')
    if state == "ready":
        p.append('<circle cx="92" cy="228" r="40" fill="url(#glow)"/>')
        p.append('<ellipse cx="92" cy="230" rx="21" ry="27" fill="#fff7ea" stroke="#e8d3ae" stroke-width="2"/>')
        p.append(star(60, 190, 7) + star(126, 196, 5) + star(80, 176, 4))
    return "".join(p)


def sticker_chicken(state):
    """A: samolepka — stejna geometrie jako soft, ale flat barvy + jednotny tlusty obrys.
    Trik: silueta = shapes vykreslene 2x (vespod stroke-only rozsirene = obrys, navrch fill)."""
    p = []
    legs = state != "hungry"
    wrap_open, wrap_close = "", ""
    if state == "hungry":
        wrap_open = '<g transform="translate(0,20) scale(1,0.92)" filter="url(#dim)">'
        wrap_close = "</g>"
    elif state == "ready":
        wrap_open = '<g transform="rotate(-3 180 170)">'
        wrap_close = "</g>"
    p.append(wrap_open)
    tail = [(104, 128, 27, 16, -32), (96, 146, 25, 14, -8), (101, 162, 22, 12, 14)]
    silhouette = ['<ellipse cx="170" cy="165" rx="78" ry="60"/>',
                  '<ellipse cx="213" cy="125" rx="42" ry="48"/>',
                  '<circle cx="238" cy="95" r="36"/>']
    tail_sh = [f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" transform="rotate({rot} {cx} {cy})"/>'
               for (cx, cy, rx, ry, rot) in tail]
    comb_shape = '<circle cx="222" cy="60" r="11"/><circle cx="238" cy="52" r="12"/><circle cx="254" cy="60" r="11"/>'
    beak_shape = '<polygon points="270,88 297,97 270,107"/>'
    wattle_shape = '<ellipse cx="261" cy="118" rx="8" ry="12"/>'
    comb_t = ' transform="rotate(14 238 62)"' if state == "hungry" else ""
    if legs:
        p.append(f'<g stroke="{OUT}" stroke-width="10" stroke-linecap="round"><path d="M150 222 L148 250 M140 250 L158 250"/><path d="M192 222 L194 250 M184 250 L202 250"/></g>')
        p.append('<g stroke="#e8931f" stroke-width="4.5" stroke-linecap="round"><path d="M150 222 L148 250 M140 250 L158 250"/><path d="M192 222 L194 250 M184 250 L202 250"/></g>')
    # 1) obrysova vrstva (rozsirena silueta vseho)
    p.append(f'<g fill="{OUT}" stroke="{OUT}" stroke-width="11">')
    p.append(f'<g{comb_t}>{comb_shape}</g>{beak_shape}{wattle_shape}')
    p.extend(tail_sh)
    p.extend(silhouette)
    p.append("</g>")
    # 2) fill vrstva
    p.append(f'<g{comb_t} fill="#ff5a5a">{comb_shape}</g>')
    p.append(f'<g fill="{BEAK}">{beak_shape}</g><polygon points="270,97 297,97 270,107" fill="#e8931f"/>')
    p.append(f'<g fill="#ff5a5a">{wattle_shape}</g>')
    p.append(f'<g fill="#f7ba49">{"".join(tail_sh)}</g>')
    p.append(f'<g fill="{CREAM}">{"".join(silhouette)}</g>')
    # bricho + kridlo flat
    p.append(f'<path d="M104 190 q40 34 96 16 q-16 32 -62 26 q-38 -6 -34 -42 z" fill="{SHADE}"/>')
    p.append(f'<ellipse cx="152" cy="170" rx="42" ry="30" fill="{SHADE}" stroke="{OUT}" stroke-width="5" transform="rotate(-14 152 170)"/>')
    # oko
    if state == "hungry":
        p.append(f'<path d="M240 92 q7 5 14 0" stroke="{OUT}" stroke-width="5" fill="none" stroke-linecap="round"/>')
    elif state == "ready":
        p.append(f'<path d="M240 88 q7 -7 14 0" stroke="{OUT}" stroke-width="5" fill="none" stroke-linecap="round"/>')
    else:
        p.append(f'<circle cx="247" cy="90" r="6" fill="{OUT}"/><circle cx="249.5" cy="87.5" r="2" fill="#fff"/>')
    p.append(wrap_close)
    if state == "hungry":
        p.append(f'<g fill="{DIM}" font-family="Segoe UI" font-weight="700">'
                 '<text x="285" y="70" font-size="26">z</text>'
                 '<text x="302" y="52" font-size="20">z</text></g>')
    if state == "ready":
        p.append('<circle cx="92" cy="228" r="40" fill="url(#glow)"/>')
        p.append(f'<ellipse cx="92" cy="230" rx="21" ry="27" fill="#fff7ea" stroke="{OUT}" stroke-width="6"/>')
        p.append(star(60, 190, 7) + star(126, 196, 5))
    return "".join(p)


def geo_chicken(state):
    """C: geometricky minimal — ciste tvary, zadne obrysy."""
    p = []
    wrap_open, wrap_close = "", ""
    if state == "hungry":
        wrap_open = '<g transform="translate(0,16)" filter="url(#dim)">'
        wrap_close = "</g>"
    p.append(wrap_open)
    p.append('<path d="M118 170 a62 62 0 0 1 62 -62 l0 62 z" fill="#e0c79b"/>')
    p.append(f'<circle cx="180" cy="170" r="62" fill="{CREAM}"/>')
    p.append(f'<circle cx="243" cy="106" r="30" fill="{CREAM}"/>')
    p.append('<circle cx="243" cy="63" r="12" fill="#ff5a5a"/>')
    p.append(f'<polygon points="271,98 297,106 271,114" fill="{BEAK}"/>')
    if state == "hungry":
        p.append('<rect x="244" y="98" width="14" height="4" rx="2" fill="#33220f"/>')
    elif state == "ready":
        p.append('<path d="M246 100 q6 -6 12 0" stroke="#33220f" stroke-width="4" fill="none" stroke-linecap="round"/>')
    else:
        p.append('<circle cx="252" cy="100" r="5" fill="#33220f"/>')
    p.append(f'<circle cx="168" cy="172" r="26" fill="{SHADE}"/>')
    if state != "hungry":
        p.append(f'<rect x="152" y="228" width="7" height="26" rx="3" fill="{LEG}"/>'
                 f'<rect x="192" y="228" width="7" height="26" rx="3" fill="{LEG}"/>')
    p.append(wrap_close)
    if state == "hungry":
        p.append(f'<g fill="{DIM}" font-family="Segoe UI" font-weight="600">'
                 '<text x="288" y="66" font-size="24">z</text>'
                 '<text x="304" y="48" font-size="17">z</text></g>')
    if state == "ready":
        p.append(f'<circle cx="96" cy="226" r="34" fill="none" stroke="{GOLD}" stroke-width="3" opacity="0.7"/>')
        p.append('<ellipse cx="96" cy="226" rx="19" ry="25" fill="#fff7ea"/>')
    return "".join(p)


PIX = {
    "producing": [
        ".......RR.........",
        "......RRRR........",
        "......OWWWO.......",
        ".....OWWWWWO......",
        ".....OWEWWWOKK....",
        ".....OWWWWWOR.....",
        "..OO..OWWWWO......",
        ".OWWOOWWWWWWO.....",
        ".OWWWWWWWWWWO.....",
        ".OSWWWWWWWWWO.....",
        ".OSSWWWWWWWO......",
        "..OSSWWWWWO.......",
        "...OOOOOOO........",
        "....L....L........",
        "...LL....LL.......",
    ],
    "hungry": [
        "..............G...",
        "......RR.....G....",
        ".....RRRR...G.....",
        "......OWWWO.......",
        ".....OWWWWWO......",
        ".....OWOWWWOKK....",
        ".....OWWWWWOR.....",
        "..OO..OWWWWO......",
        ".OWWOOWWWWWWO.....",
        ".OWWWWWWWWWWO.....",
        ".OSWWWWWWWWWO.....",
        ".OSSWWWWWWWO......",
        "..OSSWWWWWO.......",
        "...OOOOOOO........",
        "..................",
    ],
    "ready": [
        ".......RR.........",
        "......RRRR........",
        "......OWWWO.......",
        ".....OWWWWWO......",
        ".....OWEWWWOKK....",
        ".....OWWWWWOR.....",
        "..OO..OWWWWO...G..",
        ".OWWOOWWWWWWO.....",
        ".OWWWWWWWWWWO.....",
        ".OSWWWWWWWWWO.....",
        "DDOSSWWWWWWO......",
        "DDDDSSWWWWO.......",
        "DDDDOOOOOO........",
        ".DD.L....L........",
        "...LL....LL.......",
    ],
}
PIXC = {"O": "#3a2410", "W": CREAM, "S": SHADE, "R": "#ff5a5a", "K": BEAK,
        "E": "#33220f", "L": LEG, "G": GOLD, "D": "#fff7ea"}


def pixel_chicken(state):
    px = 12
    rows = PIX[state]
    ox = (CARD_W - len(rows[0]) * px) / 2
    oy = 40
    out = [f'<g shape-rendering="crispEdges"{" filter=\'url(#dim)\'" if state == "hungry" else ""}>']
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in PIXC:
                out.append(f'<rect x="{ox + x * px:.0f}" y="{oy + y * px:.0f}" width="{px}" height="{px}" fill="{PIXC[ch]}"/>')
    out.append("</g>")
    return "".join(out)


def star(cx, cy, r):
    return (f'<path d="M{cx} {cy - r} L{cx + r * 0.3} {cy - r * 0.3} L{cx + r} {cy} L{cx + r * 0.3} {cy + r * 0.3} '
            f'L{cx} {cy + r} L{cx - r * 0.3} {cy + r * 0.3} L{cx - r} {cy} L{cx - r * 0.3} {cy - r * 0.3} Z" fill="{GOLD}"/>')


STYLES = [
    ("A", "SAMOLEPKA", "flat barvy + tlusty obrys - vyrazne, snese zmenseni, komiksovy vibe", sticker_chicken),
    ("B", "MEKKA ILUSTRACE", "gradientni cozy tvary bez obrysu - nejbliz soucasne scene", soft_chicken),
    ("C", "GEOMETRIE", "ciste zakladni tvary - moderni, nejlevnejsi na vyrobu celeho setu", geo_chicken),
    ("D", "PIXEL ART", "retro 16-bit - nostalgicky, stavy se kresli snadno", pixel_chicken),
]
STATES = [("hungry", "HLADOVA", "sedi, spi, ztlumena"),
          ("producing", "VYRABI", "klidny zakladni postoj"),
          ("ready", "HOTOVO", "vejce + zlata zare")]

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Segoe UI, sans-serif">']
svg.append(f'''<defs>
<radialGradient id="softBody" cx="0.4" cy="0.35" r="0.9">
  <stop offset="0" stop-color="#fffaf0"/><stop offset="0.65" stop-color="{CREAM}"/><stop offset="1" stop-color="#efd6b0"/>
</radialGradient>
<radialGradient id="glow"><stop offset="0" stop-color="{GOLD}" stop-opacity="0.55"/><stop offset="1" stop-color="{GOLD}" stop-opacity="0"/></radialGradient>
<filter id="dim"><feColorMatrix type="saturate" values="0.4"/><feComponentTransfer><feFuncR type="linear" slope="0.75"/><feFuncG type="linear" slope="0.75"/><feFuncB type="linear" slope="0.78"/></feComponentTransfer></filter>
<radialGradient id="cardGround" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#000" stop-opacity="0.3"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
</defs>''')
svg.append(f'<rect width="{W}" height="{H}" fill="#14100a"/>')
svg.append(f'<text x="{MARGIN}" y="64" fill="{INK}" font-size="34" font-weight="800" letter-spacing="2">STATEK - NAVRHY STYLU ZVIRAT</text>')
svg.append(f'<text x="{MARGIN}" y="94" fill="{DIM}" font-size="15" letter-spacing="3">SLEPICE VE TRECH STAVECH - SCHVALOVACI BOARD - 13-07-2026</text>')
svg.append(f'<line x1="{MARGIN}" y1="110" x2="{W - MARGIN}" y2="110" stroke="#3f301c" stroke-width="1"/>')

y = TITLE_H
for key, name, desc, fn in STYLES:
    svg.append(f'<text x="{MARGIN}" y="{y + 30}" fill="{GOLD}" font-size="20" font-weight="800" letter-spacing="1">{key}</text>')
    svg.append(f'<text x="{MARGIN + 28}" y="{y + 30}" fill="{INK}" font-size="20" font-weight="700" letter-spacing="2">{name}</text>')
    svg.append(f'<text x="{MARGIN + 28}" y="{y + 48}" fill="{DIM}" font-size="12.5" letter-spacing="0.5">{desc}</text>')
    for i, (state, sname, sdesc) in enumerate(STATES):
        cx = MARGIN + i * (CARD_W + GAP)
        cy = y + ROW_LABEL + 8
        svg.append(f'<g transform="translate({cx},{cy})">')
        svg.append(f'<rect width="{CARD_W}" height="{CARD_H}" rx="18" fill="#1d150c" stroke="#3f301c"/>')
        svg.append(f'<ellipse cx="180" cy="258" rx="110" ry="16" fill="url(#cardGround)"/>')
        svg.append(fn(state))
        svg.append(f'<text x="16" y="28" fill="{DIM}" font-size="11" letter-spacing="2">{key}.{i + 1}</text>')
        svg.append("</g>")
        svg.append(f'<text x="{cx + CARD_W / 2}" y="{cy + CARD_H + 26}" fill="{INK}" font-size="13" font-weight="700" letter-spacing="2" text-anchor="middle">{sname}</text>')
        svg.append(f'<text x="{cx + CARD_W / 2}" y="{cy + CARD_H + 42}" fill="{DIM}" font-size="11" text-anchor="middle">{sdesc}</text>')
    y += ROW_LABEL + CARD_H + 46 + ROW_GAP

svg.append("</svg>")
open(r"C:\Users\ADMINI~1\AppData\Local\Temp\claude\C--Users-Administrator-webos\af93469a-0953-45f1-bed5-f9de9ee7e56b\scratchpad\statek-styleboard.svg", "w", encoding="utf-8").write("".join(svg))
print("OK", W, "x", H)
