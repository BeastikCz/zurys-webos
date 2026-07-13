**Source visual truth**
- `C:\Users\Administrator\webos\deliverables\subgoal-source-option1.png`

**Implementation evidence**
- Screenshot: `C:\Users\Administrator\webos\deliverables\subgoal-overlay-qa-final.png`
- Full comparison: `C:\Users\Administrator\webos\deliverables\subgoal-qa-comparison-final.png`
- Focused comparison: `C:\Users\Administrator\webos\deliverables\subgoal-qa-focused-final.png`
- URL/state: `http://127.0.0.1:8766/web/overlay/subgoal.html?demo=23,50,2000,2`, enabled, 23/50, Tier 3, reward 2,000.
- Viewport: 1920 × 1080 at DPR 1.

**Findings**
- No actionable P0/P1/P2 differences remain. The implementation matches the selected compact scorebug composition, safe-area position, dark angular shell, gold wheat crest, green score/progress emphasis, reward hierarchy, and purple edge accents.
- [P3] The browser-rendered system font is slightly narrower than the generated reference lettering. The hierarchy and stream-distance readability remain intact.

**Fidelity surfaces**
- Fonts and typography: condensed system stack, heavy tabular numbers, uppercase tracking, and one-line reward copy render without wrapping or clipping.
- Spacing and layout rhythm: final panel is 1320 × 124px at x=52/y=72, matching the reference's canvas proportion and upper-left safe margin.
- Colors and visual tokens: near-black shell, electric green progress, harvest gold reward/crest, and restrained purple accents match the reference.
- Image quality and asset fidelity: the reusable crest is an external SVG asset and loads successfully; no placeholder or inline-drawn logo remains.
- Copy and content: `SUB CÍL · TIER 3`, `23 / 50 SUBŮ`, and `+2 000 sedláků gifterům` match the selected state.

**Comparison history**
1. Initial implementation capture was too small relative to the selected visual. The panel width, height, safe margin, grid tracks, crest, score, and reward type were increased.
2. Final 1920 × 1080 capture confirms a 1320 × 124px panel, 46% progress fill, loaded crest, correct live values, and no browser console errors.

**Primary behavior checked**
- Existing `/api/sub-goal` polling and four-second refresh remain unchanged.
- Demo parameters populate progress, target, reward, and tier.
- Progress width resolves to 46% for 23/50.
- External crest asset loads.
- Browser console has no errors or warnings.

**Follow-up polish**
- [P3] Use a supplied brand font later if an exact licensed typeface becomes available.

final result: passed
