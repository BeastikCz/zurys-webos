**Source visual truth**
- `C:\Users\ADMINI~1\AppData\Local\Temp\codex-clipboard-88cc15f1-c633-4812-821e-686fecb679a1.png`

**Implementation screenshot**
- `C:\Users\Administrator\webos\deliverables\subgoal-overlay-qa.png`
- URL/state: `http://127.0.0.1:8765/web/overlay/subgoal.html?demo=23,50,2000,2`, enabled, 23/50, Tier 3, reward 2,000.
- Viewport: 1920 × 1080.

**Findings**
- No actionable P0/P1/P2 differences. The reference and implementation share the wide dark HUD, gold harvest crest, green primary number/progress state, target label under the bar, and right-aligned reward hierarchy.
- [P3] The supplied mock's crest has slightly more ornamental line detail than the existing reusable crest asset. This does not affect recognition or legibility.

**Fidelity surfaces**
- Fonts and typography: Rajdhani/Segoe fallback keeps the condensed uppercase hierarchy and tabular numeric emphasis; no wrapping or clipping in the 1920px capture.
- Spacing and layout rhythm: 1510 × 178px panel maintains the source's low-profile horizontal composition and balanced four-part grouping.
- Colors and visual tokens: near-black base, harvest gold, electric green, and small purple accents match the selected source.
- Image quality and asset fidelity: existing harvest crest asset is sharp at 130px and retains the source motif; no placeholder imagery is used.
- Copy and content: the implementation shows the selected Czech labels, live progress, live target, tier, and reward text.

**Comparison history**
1. Initial 1920px capture exposed reward-copy clipping at the right edge. The reward track was widened to 340px and its type size was reduced to 20px.
2. Post-fix capture shows the full reward string with no console errors. Full-view source and implementation were reviewed together; no focused crop was needed because the sole initially affected region is readable in the final full view.

**Implementation Checklist**
- [x] Preserve the live `/api/sub-goal` polling and demo query parameters.
- [x] Render the selected compact HUD layout.
- [x] Verify enabled demo state and check browser console errors.

**Follow-up Polish**
- [P3] Replace the reusable crest only if a more exact source asset becomes available.

final result: passed
