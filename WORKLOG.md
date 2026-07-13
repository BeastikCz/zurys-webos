# Worklog — sdílený deník změn (Claude + Codex + člověk)

Každý agent sem PŘIDÁ záznam hned po každé dokončené práci (změna kódu, deploy, zásah do prod DB, rozhodnutí). Nové záznamy NAHORU. Formát: datum, kdo, co, stav (lokálně / NASAZENO / prod-data).

---

## 2026-07-13

- **[Claude] AGENTS.md + WORKLOG.md** — pravidla pro Kick webhook response kódy + tenhle sdílený deník. Lokálně, untracked.
- **[Claude] Kompenzace GROOF890 (prod DB, 19:00 UTC)** — ztracený gift z 503 stormu: +1080 (`Kick gift sub 🎁 ×1 (kompenzace 503 storm 13.7.)`), record_gifter + subgoal.tick(1), +1 spin. Turbo žeton nepřidán (měl 3/3 z ručního grantu 18:02 UTC — kdo?). Kick leaderboard ověřen: chyběl přesně 1 gift; user rozhodl žetony navíc nechat.
- **[Claude] FIX webhook 503 storm — NASAZENO (~18:52 UTC, cache 2026071228)** — commit `8401852` vracel Kicku 503 i po úspěšném zpracování → retry storm, pomalý web. Fix: `return Response(200)` v success větvi `app/routers/kickhook.py`. Ověřeno: 100 % webhooků 200, load klesl. Fix + cache bump zatím NENACOMMITNUTÉ.
- **[Codex?] Ruční grant 3 turbo žetonů GROOF890 (prod DB, 18:02 UTC)** — bez odpovídajícího gift eventu; dopiš sem prosím kontext, kdo a proč.
