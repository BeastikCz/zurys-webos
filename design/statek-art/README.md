# Statek — set zvířat ve stylu B (handoff pro Codex)

Schváleno uživatelem 13. 7. 2026: **styl B — měkká cozy ilustrace** (gradienty bez obrysu,
paleta krémová #fff3e0 / jantar #ffd75e / rumělka #ff6b6b / tmavá hněď #241505).

## Co tu je

- `statek-set-b.svg` — schválený board: 5 producentů × 3 stavy + Pes + liška (17 karet)
- `gen_animals_b.py` — generátor boardu; každé zvíře je funkce vracející SVG `<g>` obsah
  v souřadnicích karty 360×300 (zvíře stojí na y≈250, střed ~x=180). **Zdroj pravdy pro tvary.**
- `statek-styleboard.svg` + `gen_styleboard.py` — starší srovnávací board 4 stylů (A–D), jen pro kontext
- `statek-style-philosophy.md` — design filozofie palety a tvarosloví
- `statek-set-c-concept.png` — schválený vizuální směr pro malovanou variantu (styl C)
- `statek-atlas-c-key.png` — 3×6 chroma atlas malovaných zvířat. **Zdroj pravdy pro herní sprity.**
- `split_atlas.py` — chroma atlas → 17 samostatných WebP spritů do `web/img/farm/animals/`
- `statek-hero-c-source.png` — zdroj golden-hour pozadí statku

## Integrace (lokálně hotovo, NENASAZENO)

1. Vektorové SVG assety generuje `gen_animals_b.py` z funkcí boardu do `web/img/farm/animals/`
   — pojmenování `{art}-{stav}.svg`:
   - art klíče dle `FARM_ART` v app.js: chicken, goat, sheep, cow, horse (=DB klíč unicorn/Kůň),
     dog (=DB klíč horse/Pes), fox
   - stavy: `hungry`, `producing`, `ready`; pes a liška jen jeden soubor (`dog.svg`, `fox.svg`)
   - ořez: viewBox těsně kolem zvířete, ne celá karta; bez karty/ground stínu (stín dodá CSS)
   - v provozu slouží jako **fallback**, když malovaný WebP chybí
2. Malované WebP sprity dělá `split_atlas.py` ze `statek-atlas-c-key.png` (stejné názvy, `.webp`).
   Sprity se **nekrájí z atlasu za běhu** — v atlasu se zvířata místy dotýkají, takže obdélníkový
   ořez tahal do políčka kus souseda (kráva-ready měla pod sebou kus koňské hřívy). Skript maskuje
   každý sprite jeho vlastní souvislou komponentou; `SEAM` v něm ručně rozděluje jedno slepené místo.
3. Frontend `stkImg()` v app.js skládá jen cestu `{art}-{stav}.webp`; fallback řetěz je WebP → SVG → emoji.
4. Shop karty používají stav `producing`.
5. Liška se zobrazuje ve scéně, když je `f.fox` pending.
6. Nové pozadí je `web/img/farm/statek-hero-c.webp`, s fallbackem na staré WebP/SVG.
7. Deploy až po výslovném schválení: `python deploy.py --deploy`.

Pozn.: stav `ready` v boardu obsahuje produkt + zlatou záři — do per-slot assetu patří zvíře
i produkt (badge s odměnou zůstává HTML). Stav `hungry` je v boardu dělaný transformací
(translate+scale+desaturace filtrem `#dim` + zzz) — do assetu zapéct ležící pózu, ale
desaturaci nechat na CSS (`.is-hungry`), ať asset zůstává barevný pro případné jiné použití.
