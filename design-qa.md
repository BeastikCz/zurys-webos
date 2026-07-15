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
