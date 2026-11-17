# ClipIt Studio: Design Notes

**Concept:** a film cutting room for stream clippers. Cobalt film stock, a lemon "tape label" accent, and a signal-ridge backdrop that reads as audio energy over time, the same signal the agents hunt for viral peaks.

## Palette
| Token | Hex | Role |
|---|---|---|
| `--ink` | #05061A | page base, inputs |
| `--ink2` | #0D1140 | panels (used at ~60% with blur) |
| `--cobalt` | #2437FF | selection, progress start, ridge lines |
| `--lemon` | #FFE14A | primary accent: headline, scores, active states, peak markers |
| `--flare` | #FF4F7B | launch CTA, reject, chat-velocity line |
| `--mint` | #5CF2B0 | done, approved, log text |
| `--paper` | #F2EFFF | body text |

## Type
- **Bricolage Grotesque** 800, width 75: headlines, scores, numbers, buttons.
- **Hanken Grotesk** 400-700: UI and body.
- System mono only for the live agent log and timecode badges.

## Layout & Steps
- Three views (Ingest, Pipeline, Studio) switch via `.view.on`. Never set `display` on a view's ID selector without `.on`, or it will show on every step.
- **Step 1 (Ingest):** Single centered column. Hero, launch panel (max 860px), then full-width workspace card grid.
  - Launch panel options use a 6-column grid: Clips (2) + Layout (4), Subtitles (4) + Facecam (2). Collapses to one column under 700px.
- **Step 2 (Pipeline):** Dedicated real-time autonomous agent command deck.
  - **Header:** Live VOD title, demo badge (when active), active model chip (`whisper-large-v3 · GPU`), and prominent tabular elapsed timer.
  - **Global Progress Bar:** Double-striped gradient bar with sub-progress readout.
  - **Pinned Current-Task HUD:** Announcer banner linked to `aria-live="polite"` declaring exact active micro-task, candidate index (e.g., `Scoring candidate 4 of 12`), and VOD timecode range (`02:14:33–02:15:10`).
  - **Master VOD Timeline Strip:** A 100% full-width interactive timecode canvas mapping the full duration of the VOD (`vod_duration`). Paints Ingest/Whisper sweep head, Signal intelligence heat zones, Candidate Slicer windows (with prune/keep animations), Gemini Director score color-coding, and Hardware Renderer completion checkmarks. Interactive hover reveals timestamp, score, and title tooltips.
  - **Dynamic Stage Focus Pane:** Replaces empty log boxes with contextual agent visualizers:
    - *Ingest:* VOD thumbnail, resolution, duration, audio extraction progress.
    - *Whisper:* Word-by-word streaming live transcript with word confidence heat shading and timestamps.
    - *Signal:* Real dual-axis audio dB vs chat velocity chart plotted against VOD time, with peaks flagged and zero synthetic motion (only paints real data; shows flat baseline and "Awaiting signal" when idle).
    - *Slicer:* Dynamic candidate cards showing timestamp ranges, merge badges, and prune animations.
    - *Director:* Live candidate evaluation cards with circular virality score gauges, typewriter hook headlines, and keep/drop verdicts.
    - *Reframer:* Sampled frame with tracked streamer facecam bounding box and 9:16 vertical crop overlay.
    - *Renderer:* Hardware encoding telemetry cards showing fps, ETA countdown, layout variant checks, and frame progress.
    - *Export:* Verified artifact checklist with Ready-to-Post indicators.
  - **Agent Stepper (Left on Desktop, Below on Mobile):** 8 agents with status badges (`Waiting`, `Running…`, `Done`, `Halted`), individual step elapsed timers, model & device badges, and micro-task progress (`4/12`).
  - **Structured Log Console:** Grouped collapsible stage accordions, log level icons (`▶`, `✔`, `✖`, `ℹ`), timestamp prefixes, and filter chips (`All`, `Agents`, `Errors`).
  - **Mobile (< 980px):** Focus pane displayed first, followed by timeline strip, 8-agent stepper, and structured logs.
- **Step 3 (Studio):** Clip list, 9:16 phone player, AI Director inspector, and interactive crop/facecam framing adjustment modal. Stacks on small screens with the player first.

## Signature Elements
1. **Signal ridges background** (`#bgc`, drawn in `app.js`): 18 layered waveform ridges that scroll forward, lemon dots on the tallest peaks, and a slow playhead sweeping across. Static when `prefers-reduced-motion` is set.
2. **Master VOD timeline strip**: Full-VOD spatial canvas mapping candidate discovery, scoring, and rendering progress across hours of stream footage.
3. **Tape-label hook** in the inspector: tilted lemon block showing the headline burned into the clip.
4. **Visual pickers:** subtitle styles render a live sample of each look; the facecam picker is a 16:9 frame with four corner slots.

## Motion & Performance
- Zero fake motion: oscilloscope and timeline only paint when real metric payloads arrive.
- Canvas animation loops automatically suspend when data is not flowing or stream is complete.
- All transitions respect `prefers-reduced-motion: reduce`.
- Full replay buffer preserves all streamed events in memory, enabling seamless step switching (`Ingest` ↔ `Pipeline` ↔ `Studio`) and SSE reconnects without state loss.
- Built-in Mock Event Player triggered via `?demo=1` for testing the entire multi-agent lifecycle offline with a prominent "Demo data" pill.

## Copy Rules
- Sentence case. Actions keep one name across the flow: "Launch pipeline", "Approve", "Reject", "Export approved to Ready-to-Post".
- Errors say what failed and what to do next with an explicit "Back to ingest" or "Retry / Resume" action.

## Accessibility
- Viewport: `<meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=1.0, shrink-to-fit=no" />` and `overflow-x: hidden` on both `html` and `body`.
- Visible 3px lemon focus ring; pickers are real buttons with labels.
- Current-task announcements broadcasted to screen readers via `aria-live="polite"`.
- Keyboard shortcuts only fire in Studio and never while typing in a field.
- Color is never the only signal: approval status and agent states also show explicit text labels.
