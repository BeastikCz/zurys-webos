# Worklog — sdílený deník změn (Claude + Codex + člověk)

Každý agent sem PŘIDÁ záznam hned po každé dokončené práci (změna kódu, deploy, zásah do prod DB, rozhodnutí). Nové záznamy NAHORU. Formát: datum, kdo, co, stav (lokálně / NASAZENO / prod-data).

---

## 2026-07-13

- **[Claude] Statek art set styl B - PODKLADY pro Codex (design/statek-art/)** - uzivatel schvalil styl B (mekka cozy ilustrace); vygenerovan kompletni SVG set: 5 producentu x 3 stavy (hladove lezi+spi / vyrabi / hotovo s produktem a zari) + pes hlidac + liska. Zdroj tvaru = gen_animals_b.py (funkce per zvire), integracni plan v design/statek-art/README.md (export per-asset SVG do web/img/farm/animals/, stavovy stkImg s fallbackem na .webp/emoji). Integraci stavi Codex.

- **[Claude] FIX neviditelny countdown v idle CTA statku + cache bump dira — NASAZENO (cache 2026071230, commit 4fc5f52)** — divaci videli "Slepice doda vejce za" bez casu a zlate tlacitko: (1) globalni .grd-time ma accent barvu = zlata na zlatem -> inline color:inherit; (2) bump_version nahrazoval jen aktualni verzi -> farm.css pridana pozdeji visela na ?v=2026071018 a prohlizece+SW shell drzely starou CSS; bump ted regexem sjednoti VSECHNY ?v= (index.html, sw.js, app.js), selftest rozsiren. Overeno: prod index+sw.js jednotne 2026071230, lokalne po SW updatu tmava idle lista s tikajicim casem.

- **[Claude] Statek UX batch (nakrmit vse / stavy zvirat / nejblizsi udalost) — NASAZENO 13.7. (cache 2026071229, commit 99b5ebc)** — backend farm.feed_all + POST /farm/feed-all (krmivo prednostne, preskoci nedostatek; test pridan, 19/19 pass). Frontend: zelene CTA Nakrmit vse (N× · cena) pod scenou (>=2 hladova; odhad ceny pocita krmivo zdarma), stavove tridy .is-hungry (ztlumene + spici zzz) / .is-growing / .is-ready (glow) / .is-fox, idle lista misto Zatim neni co sebrat ukazuje nejblizsi produkt (Slepice doda vejce za 1h 59m, zivy countdown). Soubory: app/farm.py, app/routers/misc.py, web/app.js, web/farm.css, tests/test_farm.py. Overeno v prohlizeci na dev DB, predeploy OK.

- **[Codex] Synchronizace nasazeného stavu do `origin/main` — NASAZENO** — ekonomická pojistka a reporting, bezpečnostní opravy OAuth/webpush, regresní testy, UI polish a subgoal overlay včetně crest SVG. Všechny změněné soubory z `app/` a `web/` ověřeny SHA-256 proti aktivní Contabo release; pouze commit + push, bez deploye.
- **[Claude] AGENTS.md + WORKLOG.md** — pravidla pro Kick webhook response kódy + tenhle sdílený deník. Lokálně, untracked.
- **[Claude] Kompenzace GROOF890 (prod DB, 19:00 UTC)** — ztracený gift z 503 stormu: +1080 (`Kick gift sub 🎁 ×1 (kompenzace 503 storm 13.7.)`), record_gifter + subgoal.tick(1), +1 spin. Turbo žeton nepřidán (měl 3/3 z ručního grantu 18:02 UTC — kdo?). Kick leaderboard ověřen: chyběl přesně 1 gift; user rozhodl žetony navíc nechat.
- **[Claude] FIX webhook 503 storm — NASAZENO (~18:52 UTC, cache 2026071228)** — commit `8401852` vracel Kicku 503 i po úspěšném zpracování → retry storm, pomalý web. Fix: `return Response(200)` v success větvi `app/routers/kickhook.py`. Ověřeno: 100 % webhooků 200, load klesl. Fix + cache bump zatím NENACOMMITNUTÉ.
- **[Codex] Jednorázový backfill turbo žetonů (prod DB, 18:02 UTC) — prod-data** — nebyl to individuální grant. Doplnil jsem platné gift suby zaznamenané před spuštěním turbo funkce 10. 7. 18:47:58 UTC, ještě v sedmidenní platnosti a s limitem 3 žetony na účet: 147 žetonů pro 131 účtů. GROOF890 měl odpovídající historické gifty, proto dostal 3/3. Před zásahem vznikla ověřená SQLite záloha; transakce měla idempotentní marker `_turbo_prelaunch_backfill_20260713` a kontrolu, že nikdo nepřekročil limit.
