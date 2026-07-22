# ZURYS Subs Overlay — Claude Handoff

> ✅ **VYŘEŠENO (1.7.2026):** sub/resub/gift alerty live od 21.6. (Kick → points_log →
> `/api/recent-events` poll). Donaty dořešeny 1.7. (StreamElements poller `app/se_tips.py`,
> potřebuje serverové ENV `SE_JWT` + `SE_CHANNEL_ID`). Replay protection = id kurzory + baseline.
> Dokument zůstává jako reference (OBS URL, event API, zvuky, tiery).

## Goal

Finish and integrate a farm-themed stream alert system matching `zurys.live`.
Current implementation already provides visuals, animation, alert queueing, a Gift Train,
SUB-goal progress animation, responsive layout, and original WAV sound stingers.

## Main files

- `web/overlay/alerts.html` — primary alert system for SUB, RESUB, gift subs, and donations.
- `web/overlay/subgoal.html` — persistent SUB-goal overlay.
- `web/overlay/topgifter.html` — persistent daily top-gifters overlay.
- `web/sedlak-cut.png` — transparent farmer mascot.
- `web/audio/alerts/*.wav` — eight original farm-themed sound stingers.
- `design/giftsub-overlay-concepts.html` — interactive design and tier reference.
- `scripts/generate_alert_sounds.py` — deterministic WAV generator.

## Preview URLs

Run a static server from repository root:

```powershell
python -m http.server 8765 --bind 127.0.0.1
```

Open:

- Full studio: `http://127.0.0.1:8765/web/overlay/alerts.html?studio=1`
- SUB-only sequence: `http://127.0.0.1:8765/web/overlay/alerts.html?studio=1&set=subs`
- Specific event: `http://127.0.0.1:8765/web/overlay/alerts.html?studio=1&event=gift:100`
- Design reference: `http://127.0.0.1:8765/design/giftsub-overlay-concepts.html`

## OBS URLs

Use a 1920×1080 Browser Source. Do not include `studio=1` in OBS.

- Production overlay: `https://zurys.live/overlay/alerts.html`
- Visual demo loop: `https://zurys.live/overlay/alerts.html?demo&set=subs`
- SUB goal: `https://zurys.live/overlay/subgoal.html`
- Top gifters: `https://zurys.live/overlay/topgifter.html`

## Event API

The page exposes a local queue API:

```js
window.ZurysAlerts.enqueue({
  kind: "gift", // "sub" | "resub" | "gift" | "donate"
  value: 25,
  name: "Martech1",
  subtitle: "Optional custom subtitle"
});
```

It also accepts `postMessage` payloads:

```js
window.postMessage({
  type: "zurys-alert",
  kind: "gift",
  value: 25,
  name: "Martech1"
}, "*");
```

## Supported tiers

- SUB and RESUB.
- Gift subs: `1, 5, 10, 15, 20, 25, 50, 100`.
- Donations: `30, 50, 69, 100, 120, 200, 500, 600, 700, 900, 1000, 2000 CZK`.

## Sound mapping

- SUB: rooster + barn bell.
- RESUB: accordion.
- Small gifts/donations: coins.
- 10×: barn bell.
- 20×: tractor.
- 50×: polka.
- 100× and 1000+ CZK: harvest horn + thunder.
- 69 CZK: comedy sting.

## Integration work still needed

1. Connect Kick `channel.subscription.new`, renewal, and gift webhook results to an alert feed.
2. Choose donation provider and map its webhook payload into the event API.
3. Add durable event IDs and replay protection.
4. Deliver alerts to OBS through SSE or WebSocket; polling is acceptable as fallback.
5. Keep the existing visual queue so simultaneous events never overlap.
6. Escape all external names/messages and cap displayed lengths.

## Important behavior

- `studio=1` enables controls and a fake background; never use it in OBS.
- Default page background is transparent.
- Browser audio needs a user gesture in ordinary browsers. OBS Browser Source normally permits playback.
- Keep the farmer mascot and gold/green farm palette consistent with `zurys.live`.
