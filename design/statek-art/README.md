# Statek — set zvířat ve stylu B (handoff pro Codex)

Schváleno uživatelem 13. 7. 2026: **styl B — měkká cozy ilustrace** (gradienty bez obrysu,
paleta krémová #fff3e0 / jantar #ffd75e / rumělka #ff6b6b / tmavá hněď #241505).

## Co tu je

- `statek-set-b.svg` — schválený board: 5 producentů × 3 stavy + Pes + liška (17 karet)
- `gen_animals_b.py` — generátor boardu; každé zvíře je funkce vracející SVG `<g>` obsah
  v souřadnicích karty 360×300 (zvíře stojí na y≈250, střed ~x=180). **Zdroj pravdy pro tvary.**
- `statek-styleboard.svg` + `gen_styleboard.py` — starší srovnávací board 4 stylů (A–D), jen pro kontext
- `statek-style-philosophy.md` — design filozofie palety a tvarosloví

## Zamýšlená integrace (ještě NEpostaveno)

1. Vyexportovat jednotlivé assety z funkcí v `gen_animals_b.py` jako samostatné SVG do
   `web/img/farm/animals/` — pojmenování `{art}-{stav}.svg`:
   - art klíče dle `FARM_ART` v app.js: chicken, goat, sheep, cow, horse (=DB klíč unicorn/Kůň),
     dog (=DB klíč horse/Pes), fox
   - stavy: `hungry`, `producing`, `ready`; pes a liška jen jeden soubor (`dog.svg`, `fox.svg`)
   - ořez: viewBox těsně kolem zvířete, ne celá karta; bez karty/ground stínu (stín dodá CSS)
2. Frontend `stkImg()` v app.js: vybírat podle stavu slotu (`s.state`), fallback řetěz
   `{art}-{stav}.svg` → `{art}.webp` → emoji (mechanismus `stkFixImgs` už existuje).
3. Shop karty používají stav `producing`.
4. Liška: zobrazit ve scéně, když `f.fox` pending (dnes jen banner nahoře).
5. Deploy: `python deploy.py --deploy` (cache bump už sjednocuje všechny `?v=`), potom ověřit.

Pozn.: stav `ready` v boardu obsahuje produkt + zlatou záři — do per-slot assetu patří zvíře
i produkt (badge s odměnou zůstává HTML). Stav `hungry` je v boardu dělaný transformací
(translate+scale+desaturace filtrem `#dim` + zzz) — do assetu zapéct ležící pózu, ale
desaturaci nechat na CSS (`.is-hungry`), ať asset zůstává barevný pro případné jiné použití.
