# Design source — Editorial Ink

The design canvas these files seed:
<https://claude.ai/code/artifact/2c82a697-18f1-4d34-8e72-0d4398c6e243>

Each `.dc.html` is one **artboard** — a self-contained HTML document rendered
as its own frame on a pan/zoom canvas. `canvas.json` places them and picks
the view a fresh open lands on. These are the source of truth: the published
canvas is regenerated *from* them, never edited back into them by hand.

| File | What it is |
|---|---|
| `Main.dc.html` | The landing page, 1440×3160 |
| `Identity.dc.html` | The mark, its construction, the palette and type ramp |
| `Graph.dc.html` | Concept-graph explorer |
| `Quiz.dc.html` | Adaptive quiz, showing the wrong-answer state |
| `Certificate.dc.html` | Build certificate, showing a withheld emission |
| `canvas.json` | Frame positions, titles, sticky notes, launch view |

## The system

Warm paper, ink black, one oxblood accent, and two semantic hues at matched
OKLCH lightness so *held* and *partial* read as siblings of the accent rather
than as a traffic light bolted on:

| Token | Hex | Role |
|---|---|---|
| paper | `#FAF8F4` | ground |
| panel | `#F2EEE6` | raised surface |
| rule | `#E0D9CB` | hairlines |
| ink-muted | `#6B655C` | secondary text |
| ink | `#12110F` | body |
| oxblood | `#7A2E2E` | the accent — and *contradicted* |
| moss | `#2F5A38` | entailed, held |
| ochre | `#7A5A16` | unsupported, partial |

Instrument Serif (display) / Newsreader (body) / IBM Plex Mono (data).

The organising idea is that **the margin is a first-class column** —
citations, diagnostics and provenance live there, which is exactly the claim
the compiler makes about its own output. Motion is "ink settles": a wipe from
the left, then a rise out of blur. Nothing bounces.

`src/coursec/web/static/` implements this same system in the shipped
frontend; `app.css` carries the identical tokens.

## Regenerating the canvas

The canvas is assembled by the `design` skill's helper, which bakes these
files into a copy of its editor payload. That seeded output is ~2.5 MB of
editor code and is deliberately **not** committed — it is a build artifact,
regenerable at any time, and `.gitignore` keeps it out:

```bash
node "<skill base dir>/seed-canvas.mjs" \
  --template "<skill base dir>/payload.template.html" \
  --out coursec-editorial-ink.html \
  --title "CourseC — Editorial Ink" \
  --artboard Main.dc.html --artboard Identity.dc.html \
  --artboard Graph.dc.html --artboard Quiz.dc.html \
  --artboard Certificate.dc.html \
  --canvas canvas.json
```

Then publish that file to the artifact URL above. Editing the canvas in the
browser and saving publishes a new version there without touching this
directory, so if the two drift, pull the canvas back down before editing
these files again.

## A note on the numbers

Copy in these artboards uses figures from the real project (the 0.855 /
0.764 merge-threshold fixtures, `n = 12` for the pilot screen, the
inverse-square worked example). They are illustrative of a build, not a
snapshot of one — the shipped frontend reads live rows instead.
