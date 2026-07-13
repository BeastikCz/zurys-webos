# -*- coding: utf-8 -*-
"""Statek — plny set zvirat ve stylu B (mekka cozy ilustrace).
5 producentu x 3 stavy + pes + liska. Vystup: prehledovy SVG board."""

CARD_W, CARD_H, GAP = 360, 300, 20
MARGIN = 80
W = MARGIN * 2 + CARD_W * 3 + GAP * 2
ROW_LABEL, ROW_GAP = 52, 38
TITLE_H = 130

INK = "#f6eedc"
DIM = "#9e8a69"
GOLD = "#ffd75e"
DARK = "#33220f"


def star(cx, cy, r):
    return (f'<path d="M{cx} {cy - r} L{cx + r * 0.3} {cy - r * 0.3} L{cx + r} {cy} L{cx + r * 0.3} {cy + r * 0.3} '
            f'L{cx} {cy + r} L{cx - r * 0.3} {cy + r * 0.3} L{cx - r} {cy} L{cx - r * 0.3} {cy - r * 0.3} Z" fill="{GOLD}"/>')


def zzz():
    return (f'<g fill="{DIM}" font-family="Segoe UI" font-weight="600">'
            '<text x="290" y="66" font-size="26">z</text>'
            '<text x="306" y="48" font-size="20">z</text>'
            '<text x="318" y="34" font-size="15">z</text></g>')


def eye(x, y, state, r=5):
    if state == "hungry":
        return f'<path d="M{x - 7} {y} q7 5 14 0" stroke="{DARK}" stroke-width="3.5" fill="none" stroke-linecap="round"/>'
    if state == "ready":
        return f'<path d="M{x - 7} {y} q7 -7 14 0" stroke="{DARK}" stroke-width="3.5" fill="none" stroke-linecap="round"/>'
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{DARK}"/><circle cx="{x + 2}" cy="{y - 2}" r="{r * 0.35}" fill="#fff"/>'


def wrap(state, inner, sit_dy=24):
    if state == "hungry":
        return f'<g transform="translate(0,{sit_dy}) scale(1,0.9)" filter="url(#dim)">{inner}</g>' + zzz()
    if state == "ready":
        return f'<g transform="rotate(-2 180 170)">{inner}</g>'
    return inner


def quad_legs(color, hoof, xs=(112, 142, 202, 232), top=195, bottom=248):
    p = []
    for x in xs:
        p.append(f'<rect x="{x}" y="{top}" width="11" height="{bottom - top}" rx="5" fill="{color}"/>')
        p.append(f'<rect x="{x - 1}" y="{bottom - 10}" width="13" height="10" rx="4" fill="{hoof}"/>')
    return "".join(p)


def glow_product(inner):
    return f'<circle cx="80" cy="225" r="42" fill="url(#glow)"/>{inner}' + star(48, 188, 7) + star(116, 192, 5)


# ---------- produkty ----------
def prod_egg():
    return glow_product('<ellipse cx="80" cy="228" rx="20" ry="26" fill="#fff7ea" stroke="#e8d3ae" stroke-width="2"/>')


def prod_milk():
    return glow_product(
        '<path d="M62 206 L98 206 L94 250 L66 250 Z" fill="url(#pail)"/>'
        '<ellipse cx="80" cy="206" rx="18" ry="6" fill="#fffdf6"/>'
        '<path d="M62 206 a18 12 0 0 1 36 0" stroke="#8d9aa8" stroke-width="3" fill="none"/>')


def prod_wool():
    c = "#fdf6ea"
    return glow_product(
        f'<circle cx="68" cy="230" r="16" fill="{c}"/><circle cx="92" cy="230" r="16" fill="{c}"/>'
        f'<circle cx="80" cy="218" r="16" fill="{c}"/><circle cx="80" cy="238" r="13" fill="{c}"/>'
        f'<path d="M66 228 a14 14 0 1 1 14 14" stroke="#dcc9a8" stroke-width="2.5" fill="none" stroke-linecap="round"/>')


def prod_cheese():
    return glow_product(
        '<path d="M56 226 a24 10 0 0 1 48 0 l0 12 a24 10 0 0 1 -48 0 z" fill="#f3c04b"/>'
        '<ellipse cx="80" cy="226" rx="24" ry="10" fill="#ffd975"/>'
        '<circle cx="72" cy="225" r="3.4" fill="#e3a92f"/><circle cx="88" cy="228" r="2.6" fill="#e3a92f"/>')


def prod_manure():
    b = "#8a5a30"
    return glow_product(
        f'<ellipse cx="80" cy="242" rx="24" ry="10" fill="{b}"/>'
        f'<ellipse cx="80" cy="232" rx="17" ry="9" fill="#9a6636"/>'
        f'<ellipse cx="80" cy="222" rx="10" ry="7" fill="#a97239"/>'
        f'<path d="M80 208 q8 4 2 10" stroke="#a97239" stroke-width="5" fill="none" stroke-linecap="round"/>')


# ---------- zvirata ----------
def chicken(state):
    p = []
    for (cx, cy, rx, ry, rot) in [(104, 128, 27, 16, -32), (96, 146, 25, 14, -8), (101, 162, 22, 12, 14)]:
        p.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="#e8c9a0" transform="rotate({rot} {cx} {cy})"/>')
    p.append('<ellipse cx="170" cy="165" rx="78" ry="60" fill="url(#cream)"/>')
    p.append('<ellipse cx="213" cy="125" rx="42" ry="48" fill="url(#cream)"/>')
    p.append('<circle cx="238" cy="95" r="36" fill="url(#cream)"/>')
    comb = ('<g fill="#ff6b6b"><circle cx="222" cy="60" r="11"/><circle cx="238" cy="52" r="12"/>'
            '<circle cx="254" cy="60" r="11"/></g>')
    p.append(f'<g transform="rotate(14 238 62)">{comb}</g>' if state == "hungry" else comb)
    p.append('<polygon points="270,88 297,97 270,107" fill="#ffb340"/>')
    p.append('<polygon points="270,97 297,97 270,107" fill="#e8931f"/>')
    p.append('<ellipse cx="261" cy="118" rx="8" ry="12" fill="#ff6b6b"/>')
    p.append(eye(247, 90, state))
    p.append('<ellipse cx="152" cy="170" rx="42" ry="30" fill="#f0d2ab" transform="rotate(-14 152 170)"/>')
    p.append('<path d="M120 178 q30 16 62 6 M126 190 q26 12 50 4" stroke="#d9b586" stroke-width="3" fill="none" stroke-linecap="round"/>')
    if state != "hungry":
        p.append('<g stroke="#d98a2b" stroke-width="5" stroke-linecap="round">'
                 '<path d="M150 222 L148 250 M140 250 L158 250"/>'
                 '<path d="M192 222 L194 250 M184 250 L202 250"/></g>')
    out = wrap(state, "".join(p))
    return out + (prod_egg() if state == "ready" else "")


def goat(state):
    p = []
    # rohy dozadu
    p.append('<path d="M262 62 q26 -26 44 -14 q-22 2 -30 24 z" fill="#8a6a3f"/>')
    p.append('<path d="M250 60 q10 -30 32 -30 q-16 10 -18 34 z" fill="#9d7c4e"/>')
    if state != "hungry":
        p.append(quad_legs("#c99a56", "#8a6a3f"))
    p.append('<path d="M92 128 q-16 -10 -10 -28 q10 12 22 12 z" fill="#c99a56"/>')  # ocasek nahoru
    p.append('<ellipse cx="170" cy="160" rx="85" ry="52" fill="url(#tan)"/>')
    p.append('<ellipse cx="240" cy="112" rx="38" ry="42" fill="url(#tan)"/>')      # krk+hlava
    p.append('<ellipse cx="268" cy="92" rx="30" ry="26" fill="url(#tan)"/>')
    # usi svesene
    p.append('<ellipse cx="238" cy="84" rx="10" ry="20" fill="#b5854a" transform="rotate(24 238 84)"/>')
    # cumak + bradka
    p.append('<ellipse cx="290" cy="102" rx="15" ry="12" fill="#e9c78f"/>')
    p.append('<circle cx="295" cy="99" r="2.2" fill="#7a5a35"/>')
    p.append('<path d="M284 112 q4 18 -6 24 q-4 -14 -2 -22 z" fill="#b5854a"/>')
    p.append(eye(268, 88, state))
    p.append('<ellipse cx="150" cy="165" rx="44" ry="30" fill="#c08e4e" opacity="0.55" transform="rotate(-10 150 165)"/>')
    out = wrap(state, "".join(p))
    return out + (prod_milk() if state == "ready" else "")


def sheep(state):
    p = []
    if state != "hungry":
        p.append(quad_legs("#6a4a33", "#4a3222"))
    # vlneny mrak
    wool = "url(#wool)"
    for (cx, cy, r) in [(120, 150, 42), (160, 130, 46), (205, 138, 44), (230, 160, 40),
                        (205, 185, 42), (160, 192, 44), (120, 180, 40), (168, 160, 52)]:
        p.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{wool}"/>')
    # hlava tmava
    p.append('<ellipse cx="262" cy="112" rx="27" ry="30" fill="url(#face)"/>')
    p.append('<ellipse cx="240" cy="106" rx="16" ry="8" fill="#5a3d2a" transform="rotate(-24 240 106)"/>')  # ucho
    p.append('<ellipse cx="284" cy="112" rx="16" ry="8" fill="#5a3d2a" transform="rotate(24 284 112)"/>')
    # vlnena celka
    p.append(f'<circle cx="252" cy="86" r="14" fill="{wool}"/><circle cx="268" cy="82" r="12" fill="{wool}"/>')
    p.append(eye(258, 110, state, r=4.5))
    p.append('<ellipse cx="266" cy="128" rx="8" ry="5" fill="#8a6a50"/>')  # cumacek
    out = wrap(state, "".join(p))
    return out + (prod_wool() if state == "ready" else "")


def cow(state):
    p = []
    if state != "hungry":
        p.append(quad_legs("#f3e6d0", "#6a4a33"))
    p.append('<path d="M88 140 q-20 4 -18 24 q8 -4 16 -2 z" fill="#f3e6d0"/>')
    p.append('<circle cx="72" cy="168" r="9" fill="#e8c9a0"/>')     # ocas strapec
    p.append('<ellipse cx="170" cy="160" rx="88" ry="54" fill="url(#cream)"/>')
    # flaky
    p.append('<path d="M120 128 q30 -14 52 4 q10 22 -12 34 q-34 6 -46 -12 q-6 -16 6 -26 z" fill="#4a3626"/>')
    p.append('<ellipse cx="216" cy="188" rx="26" ry="18" fill="#4a3626"/>')
    # vemeno
    p.append('<ellipse cx="180" cy="208" rx="24" ry="14" fill="#f2b8c6"/>')
    # hlava
    p.append('<ellipse cx="252" cy="106" rx="34" ry="32" fill="url(#cream)"/>')
    p.append('<path d="M228 88 q6 -18 24 -12 q-10 10 -8 18 z" fill="#4a3626"/>')  # flek na hlave
    # rohy + usi
    p.append('<path d="M232 74 q-4 -16 12 -18 q-4 10 0 16 z" fill="#e9c78f"/>')
    p.append('<path d="M270 72 q6 -16 -10 -20 q4 10 -2 16 z" fill="#e9c78f"/>')
    p.append('<ellipse cx="222" cy="96" rx="14" ry="8" fill="#e8c9a0" transform="rotate(-18 222 96)"/>')
    p.append('<ellipse cx="282" cy="94" rx="14" ry="8" fill="#e8c9a0" transform="rotate(18 282 94)"/>')
    # cumak
    p.append('<ellipse cx="256" cy="126" rx="22" ry="13" fill="#f2b8c6"/>')
    p.append('<circle cx="248" cy="126" r="2.6" fill="#c4798f"/><circle cx="264" cy="126" r="2.6" fill="#c4798f"/>')
    p.append(eye(244, 102, state, r=4.5) + eye(268, 102, state, r=4.5))
    out = wrap(state, "".join(p))
    return out + (prod_cheese() if state == "ready" else "")


def horse(state):
    p = []
    if state != "hungry":
        p.append(quad_legs("#a5672a", "#6a4222", top=192, bottom=250))
    p.append('<path d="M92 140 q-26 10 -22 52 q14 -8 26 -8 z" fill="#5a3a1c"/>')  # ohon
    p.append('<ellipse cx="168" cy="158" rx="82" ry="50" fill="url(#brown)"/>')
    p.append('<path d="M212 130 q10 -44 44 -58 q30 -12 40 6 l-18 10 q10 2 12 12 l-22 22 q-12 22 -34 26 z" fill="url(#brown)"/>')
    # hriva
    p.append('<path d="M216 128 q6 -40 42 -56 q-8 18 -4 26 q-14 18 -16 34 z" fill="#5a3a1c"/>')
    # usi + cumak
    p.append('<path d="M266 62 q2 -16 14 -18 q0 12 -4 18 z" fill="#a5672a"/>')
    p.append('<ellipse cx="292" cy="92" rx="14" ry="11" fill="#c99a6e"/>')
    p.append('<circle cx="296" cy="90" r="2.4" fill="#5a3a1c"/>')
    p.append(eye(268, 82, state, r=4.5))
    p.append('<ellipse cx="148" cy="164" rx="42" ry="28" fill="#96591f" opacity="0.6" transform="rotate(-10 148 164)"/>')
    out = wrap(state, "".join(p))
    return out + (prod_manure() if state == "ready" else "")


def dog(_state="producing"):
    p = []
    # sedici pes (hlidac) — jediny stav
    p.append('<path d="M150 250 q-10 -66 34 -96 q40 -26 58 6 q10 22 -4 42 l4 48 z" fill="url(#dogfur)"/>')
    p.append('<path d="M148 252 q-34 -6 -28 -34 q22 2 34 18 z" fill="url(#dogfur)"/>')   # ohon zatoceny
    p.append('<circle cx="226" cy="120" r="40" fill="url(#dogfur)"/>')
    p.append('<path d="M196 92 q-6 -26 12 -30 q8 14 4 28 z" fill="#c47a3a"/>')   # usi spicate
    p.append('<path d="M250 88 q8 -26 -10 -32 q-8 14 -2 28 z" fill="#c47a3a"/>')
    p.append('<ellipse cx="238" cy="138" rx="16" ry="11" fill="#f4dcbc"/>')      # cumak
    p.append('<ellipse cx="244" cy="132" rx="4.5" ry="3.5" fill="#33220f"/>')
    p.append('<path d="M238 142 q3 7 9 6" stroke="#b06a2e" stroke-width="2.5" fill="none" stroke-linecap="round"/>')
    p.append(eye(218, 116, "ready", r=4.5))   # spokojene mhouri
    p.append('<ellipse cx="196" cy="252" rx="52" ry="8" fill="#000" opacity="0.25"/>')
    p.append('<path d="M162 250 l64 0" stroke="#c47a3a" stroke-width="10" stroke-linecap="round"/>')
    # obojek + znamka
    p.append('<path d="M198 158 q28 14 52 -4" stroke="#b4413a" stroke-width="9" fill="none"/>')
    p.append(f'<circle cx="228" cy="164" r="7" fill="{GOLD}"/>')
    return "".join(p)


def fox(_state="producing"):
    p = []
    # plizici se liska — nizky postoj
    p.append('<path d="M60 206 q-24 -18 -12 -40 q18 2 26 22 q40 -22 96 -16 q44 6 66 30" fill="none"/>')
    p.append('<path d="M64 196 q-28 -10 -20 -38 q22 0 32 22 q6 10 -12 16 z" fill="url(#foxfur)"/>')   # ohon
    p.append('<circle cx="56" cy="170" r="12" fill="#fdf6ea"/>')  # bila spicka
    p.append('<ellipse cx="160" cy="196" rx="86" ry="34" fill="url(#foxfur)"/>')
    p.append('<ellipse cx="248" cy="178" rx="34" ry="26" fill="url(#foxfur)"/>')
    p.append('<path d="M274 180 q20 2 24 10 q-14 6 -26 0 z" fill="url(#foxfur)"/>')  # protahly cenich
    p.append('<ellipse cx="252" cy="196" rx="18" ry="10" fill="#fdf6ea"/>')          # bila brada
    p.append('<circle cx="297" cy="188" r="3.6" fill="#33220f"/>')
    p.append('<path d="M226 158 q-4 -20 10 -24 q6 12 2 24 z" fill="#c9531f"/>')
    p.append('<path d="M258 154 q8 -20 -8 -26 q-6 12 -2 24 z" fill="#c9531f"/>')
    p.append('<path d="M240 172 q7 -6 14 1" stroke="#33220f" stroke-width="3.5" fill="none" stroke-linecap="round"/>')  # prihmourene oko
    p.append('<rect x="108" y="224" width="10" height="24" rx="5" fill="#7a3d16"/>')
    p.append('<rect x="196" y="224" width="10" height="24" rx="5" fill="#7a3d16"/>')
    p.append('<ellipse cx="165" cy="250" rx="90" ry="8" fill="#000" opacity="0.25"/>')
    return "".join(p)


ANIMALS = [
    ("SLEPICE", "vejce", chicken, True),
    ("KOZA", "mleko (kyblik)", goat, True),
    ("OVCE", "vlna (klubko)", sheep, True),
    ("KRAVA", "syr (bochnik)", cow, True),
    ("KUN", "hnuj (kupka)", horse, True),
]
SPECIALS = [("PES", "hlidac - pasivni bonus, jediny stav", dog),
            ("LISKA", "udalost - plizi se ke koristi", fox)]
STATES = [("hungry", "HLADOVE"), ("producing", "VYRABI"), ("ready", "HOTOVO")]

rows = len(ANIMALS) + 1
H = TITLE_H + rows * (ROW_LABEL + CARD_H + 46 + ROW_GAP) + 30

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Segoe UI, sans-serif">']
svg.append(f'''<defs>
<radialGradient id="cream" cx="0.4" cy="0.35" r="0.95"><stop offset="0" stop-color="#fffaf0"/><stop offset="0.65" stop-color="#fff3e0"/><stop offset="1" stop-color="#efd6b0"/></radialGradient>
<radialGradient id="tan" cx="0.4" cy="0.35" r="0.95"><stop offset="0" stop-color="#e3b877"/><stop offset="0.7" stop-color="#d3a35d"/><stop offset="1" stop-color="#b5854a"/></radialGradient>
<radialGradient id="wool" cx="0.4" cy="0.35" r="0.95"><stop offset="0" stop-color="#fffdf6"/><stop offset="0.7" stop-color="#f7ecd8"/><stop offset="1" stop-color="#e3d2b4"/></radialGradient>
<radialGradient id="face" cx="0.4" cy="0.35" r="0.95"><stop offset="0" stop-color="#7d5940"/><stop offset="1" stop-color="#5a3d2a"/></radialGradient>
<radialGradient id="brown" cx="0.4" cy="0.35" r="0.95"><stop offset="0" stop-color="#c98a44"/><stop offset="0.7" stop-color="#b5722f"/><stop offset="1" stop-color="#96591f"/></radialGradient>
<radialGradient id="dogfur" cx="0.4" cy="0.35" r="0.95"><stop offset="0" stop-color="#e8a860"/><stop offset="0.7" stop-color="#d98f47"/><stop offset="1" stop-color="#b06a2e"/></radialGradient>
<radialGradient id="foxfur" cx="0.4" cy="0.35" r="0.95"><stop offset="0" stop-color="#f08a3c"/><stop offset="0.7" stop-color="#e0702a"/><stop offset="1" stop-color="#c9531f"/></radialGradient>
<linearGradient id="pail" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#aeb9c6"/><stop offset="0.5" stop-color="#dfe6ee"/><stop offset="1" stop-color="#93a0ae"/></linearGradient>
<radialGradient id="glow"><stop offset="0" stop-color="{GOLD}" stop-opacity="0.55"/><stop offset="1" stop-color="{GOLD}" stop-opacity="0"/></radialGradient>
<filter id="dim"><feColorMatrix type="saturate" values="0.4"/><feComponentTransfer><feFuncR type="linear" slope="0.75"/><feFuncG type="linear" slope="0.75"/><feFuncB type="linear" slope="0.78"/></feComponentTransfer></filter>
<radialGradient id="ground" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#000" stop-opacity="0.3"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>
</defs>''')
svg.append(f'<rect width="{W}" height="{H}" fill="#14100a"/>')
svg.append(f'<text x="{MARGIN}" y="64" fill="{INK}" font-size="34" font-weight="800" letter-spacing="2">STATEK - SET ZVIRAT VE STYLU B</text>')
svg.append(f'<text x="{MARGIN}" y="94" fill="{DIM}" font-size="15" letter-spacing="3">MEKKA ILUSTRACE - 5 PRODUCENTU x 3 STAVY + PES + LISKA</text>')
svg.append(f'<line x1="{MARGIN}" y1="110" x2="{W - MARGIN}" y2="110" stroke="#3f301c" stroke-width="1"/>')


def card(cx, cy, inner, idx_label, caption):
    s = [f'<g transform="translate({cx},{cy})">']
    s.append(f'<rect width="{CARD_W}" height="{CARD_H}" rx="18" fill="#1d150c" stroke="#3f301c"/>')
    s.append('<ellipse cx="180" cy="258" rx="110" ry="16" fill="url(#ground)"/>')
    s.append(inner)
    s.append(f'<text x="16" y="28" fill="{DIM}" font-size="11" letter-spacing="2">{idx_label}</text>')
    s.append("</g>")
    s.append(f'<text x="{cx + CARD_W / 2}" y="{cy + CARD_H + 26}" fill="{INK}" font-size="13" font-weight="700" letter-spacing="2" text-anchor="middle">{caption}</text>')
    return "".join(s)


y = TITLE_H
for n, (name, prod, fn, _) in enumerate(ANIMALS):
    svg.append(f'<text x="{MARGIN}" y="{y + 30}" fill="{GOLD}" font-size="20" font-weight="800" letter-spacing="2">{name}</text>')
    svg.append(f'<text x="{MARGIN}" y="{y + 48}" fill="{DIM}" font-size="12.5">produkt: {prod}</text>')
    for i, (state, sname) in enumerate(STATES):
        cx = MARGIN + i * (CARD_W + GAP)
        svg.append(card(cx, y + ROW_LABEL + 8, fn(state), f"{name[:3]}.{i + 1}", sname))
    y += ROW_LABEL + CARD_H + 46 + ROW_GAP

svg.append(f'<text x="{MARGIN}" y="{y + 30}" fill="{GOLD}" font-size="20" font-weight="800" letter-spacing="2">SPECIALNI</text>')
for i, (name, desc, fn) in enumerate(SPECIALS):
    cx = MARGIN + i * (CARD_W + GAP)
    svg.append(card(cx, y + ROW_LABEL + 8, fn(), name, desc.upper()[:34]))
svg.append("</svg>")

open(r"C:\Users\ADMINI~1\AppData\Local\Temp\claude\C--Users-Administrator-webos\af93469a-0953-45f1-bed5-f9de9ee7e56b\scratchpad\statek-set-b.svg", "w", encoding="utf-8").write("".join(svg))
print("OK", W, "x", H)
