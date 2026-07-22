# Komunitní Trh SOON — design QA

- Source visual truth: `C:/Users/Administrator/.codex/generated_images/019f7a66-2ded-7f73-a6a8-39e6c252ca5a/exec-5930fc0e-a8e3-40dc-9ec9-a3c8eb254c71.png`
- Implementation: `http://127.0.0.1:8019/?market-soon=1#/shop`
- Intended viewport/state: desktop 1440×1024 and mobile 390×844, Shop → Trh → SOON teaser
- Implementation screenshot path: unavailable; both the selected in-app browser and Chrome timed out during local navigation/screenshot capture

## Full-view and focused comparison evidence

The selected source mock was opened at original resolution. The implementation reproduces its asymmetrical hierarchy, large left-side title, gold SOON status, fixed-price/auction cues, launch status, overlapping skin preview cards and subdued farm background using existing project assets. A browser-rendered implementation screenshot could not be captured, so a combined source/implementation comparison and focused typography/card comparison cannot be claimed.

## Required fidelity surfaces

- Fonts and typography: existing Zurys font stack with a 58px responsive display heading and 15–18px support copy; visual comparison blocked.
- Spacing and layout rhythm: two-column 430px desktop panel with responsive single-column layouts at 900px and 620px; visual comparison blocked.
- Colors and visual tokens: existing dark-brown surface, cream text and harvest-gold accent family retained; visual comparison blocked.
- Image quality and asset fidelity: existing `statek-hero-c.webp`, `stiletto-scorched.png`, `awp-printstream.png` and `klas-ready.webp` assets used; no placeholder, emoji or generated replacement asset introduced.
- Copy and content: `SOON`, `Komunitní Trh`, safe-market promise, fixed-price/auction modes and non-interactive launch state implemented.

## Findings

- [P1] Rendered fidelity remains unverified.
  - Evidence: local app and assets respond over HTTP, frontend syntax/diff checks pass, but both allowed browser surfaces time out before an implementation screenshot.
  - Impact: desktop/mobile overflow, final crop and card overlap cannot be approved visually.
  - Fix: repeat capture at 1440×1024 and 390×844 when browser control is responsive, then compare source and implementation together.

## Comparison history

1. In-app browser loaded the local Shop shell but timed out on the target tab interaction and subsequent direct navigation.
2. Chrome loaded the local URL/title but timed out waiting for `.market-soon` and on screenshot capture.
3. No visual fixes were made from unobserved output; QA remains blocked instead of claiming parity.

final result: blocked

---

# Support tickets — design QA

## Evidence

- Source references:
  - `C:/Users/ADMINI~1/AppData/Local/Temp/codex-clipboard-233aad09-f9c5-4d35-8913-8c20a990f8b0.png`
  - `C:/Users/ADMINI~1/AppData/Local/Temp/codex-clipboard-40e15960-747c-472a-98d9-683ced150da8.png`
  - `C:/Users/ADMINI~1/AppData/Local/Temp/codex-clipboard-bb4780a5-add0-441a-b54e-88066cd74306.png`
  - `C:/Users/ADMINI~1/AppData/Local/Temp/codex-clipboard-ddd083ce-5bcc-4ca5-82dc-e7c3dc3c6c82.png`
- Implementation: `http://127.0.0.1:8011/#/podpora`
- Desktop captures: `C:/Users/Administrator/AppData/Local/Temp/webos-support-qa/category.png`, `form.png`, `user-detail.png`, `admin-dashboard.png`, `admin-resolved.png`
- Mobile captures: `C:/Users/Administrator/AppData/Local/Temp/webos-support-qa/mobile-list.png`, `mobile-category.png`
- Side-by-side comparison captures: `compare-category.png`, `compare-form.png`, `compare-dashboard.png` in the same temporary directory.

## Visual comparison

- The source structure is retained: separate Support navigation, two-step category/form creation, ticket list, detail conversation, progress states, filters, and staff status controls.
- The implementation intentionally applies the existing ZURYS design system instead of copying the source palette: orange accent, existing type scale, surfaces, borders, radii, buttons, and header.
- Desktop layout is readable at 1440 × 900 and 1900 × 900. The ticket list and detail remain visible together and the reply composer stays anchored at the bottom.
- Mobile layout is readable at 390 × 844. Stats collapse to two columns, the primary CTA spans the page, the picker becomes a single-column scrollable modal, and no horizontal overflow is visible.
- Modal animation was allowed to settle before the final captures; labels, fields, cards, and actions are fully opaque and legible.

## Interaction evidence

- Playwright completed the full primary flow: user login → open category picker → select category → fill and create ticket → user reply → admin login → open ticket → admin reply → status change to resolved → mobile picker.
- Final run completed with no page errors and no unexpected HTTP failures.
- The known local-only missing `/uploads/coin.png` asset was excluded from the browser-error gate; it is unrelated to Support and is supplied by production uploads.
- Backend tests cover ticket privacy, staff transitions, closed-ticket reply blocking, and separation from private messages.

## Scope notes

- File attachments from the source reference are intentionally not included in this release; safe upload storage and moderation are a separate feature.
- No new image or icon assets were needed for the requested ZURYS-styled implementation.

final result: passed

---

# Design QA — divácká nabídka skinu

- Source truth:
  - `C:\Users\ADMINI~1\AppData\Local\Temp\codex-clipboard-6763c1de-236b-4427-a1ca-4d840b26d242.png`
  - `C:\Users\ADMINI~1\AppData\Local\Temp\codex-clipboard-24445f0e-ccae-4e93-9477-9cf922e4588d.png`
- Prototype: `http://127.0.0.1:8019/#/shop` → `Trh` → `Prodat vlastní skin`
- Intended viewport: desktop 1351×531 for the listing and 810×713 for the form state
- Intended states: fixed-price listing, auction listing, viewer submission modal, pending admin review
- Implementation screenshot path: unavailable because the selected in-app browser timed out during every post-load interaction and screenshot capture

## Full-view and focused comparison evidence

Both source screenshots were opened at original resolution. The implementation retained the visible Shop hierarchy, card treatment, modal proportions, field spacing, typography, colors, image treatment, and Czech copy. The public `Z PC` control and file input were removed; the same row now contains only a URL and the existing skin catalog. A native two-option sale-type selector and adjacent price field reuse the existing form controls.

A browser-rendered implementation screenshot could not be captured, so neither a combined full-view comparison nor a focused form/card comparison can be claimed. Source imagery and existing skin catalog assets remain unchanged; no generated or placeholder assets were added.

## Functional verification

- Fixed-price submission → admin approval → public listing with one `Koupit` action: passed on isolated test server.
- Auction submission → admin approval → 24-hour listing with bidding and no `Kup teď`: passed on isolated test server.
- Bid attempt against a fixed-price listing: rejected with HTTP 400.
- Public market upload endpoint: removed; POST returns HTTP 405.
- Pending admin queue includes the selected sale type.
- Targeted automated tests, Python compile, frontend syntax, and diff checks: passed.

## Required fidelity surfaces

- Fonts and typography: existing Shop font stack, weights, labels, and hierarchy reused; browser comparison blocked.
- Spacing and layout rhythm: existing modal, `field-row`, card, radius, and spacing tokens reused; browser comparison blocked.
- Colors and visual tokens: existing dark Shop surfaces and accent tokens reused; browser comparison blocked.
- Image quality and asset fidelity: existing skin URLs/catalog assets reused; file upload removed; no replacement assets introduced.
- Copy and content: clear `Pevná cena` / `Aukce na 24 hodin` choice, contextual price label, and unchanged approval safety warning.

## Findings history

1. Previous pass was blocked after the local browser loaded the Shop but timed out on interaction.
2. Current pass loaded the updated Shop and exposed the expected `Trh` control in the DOM.
3. Clicking `Trh`, DOM fallback, and screenshot capture each timed out again; functional verification continued through the isolated test API without claiming visual parity.

## Final result

blocked
