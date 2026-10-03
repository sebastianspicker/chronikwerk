---
name: Chronikwerk
description: Auditable ticket archiving for Zammad.
colors:
  paper: "#F8F7F4"
  paper-sunk: "#F0EEF0"
  sheet: "#FFFFFF"
  ink: "#1E1B24"
  ink-secondary: "#4F4957"
  ink-tertiary: "#6B6573"
  rule: "#DAD6DE"
  field-edge: "#857F8C"
  copying-ink: "#4B3A8A"
  ok: "#1D6B45"
  caution: "#7A5300"
  fault: "#A3222F"
typography:
  text: "Atkinson Hyperlegible Next (OFL, self-hosted variable latin subset)"
  identifiers: "Atkinson Hyperlegible Mono (OFL, self-hosted variable latin subset)"
layout:
  page-maximum: "80rem"
  margin-track: "13.5rem"
  changes-column-collapse: "75rem"
  margin-fold: "52rem"
  record-reflow: "40rem"
  reflow-minimum: "320px"
rounded:
  control: "2px"
accessibility:
  target: "WCAG 2.2 AA"
  minimum-control-height: "44px"
  focus-outline: "3px copying ink"
---

# Design system: Annalen

Chronikwerk turns ticket events into a record. Its administration interface is set like an
annal or a registry file. A **margin track** carries time and reference: section names, event
times, counts, filters, notes. The **body track** carries the entry. One continuous hairline
divides them. The full rationale, the alternatives considered and the assumptions are in
[`DESIGN_BRIEF.md`](DESIGN_BRIEF.md).

## Principles

1. State operational truth, including volatility, staleness, staged state, and restart
   boundaries.
2. Keep expert identifiers legible and visible without exposing secrets.
3. Make consequential actions explicit, specific, and recoverable: disclose, acknowledge, submit.
4. Prefer native semantics and familiar controls over custom interaction.
5. Let type and rules carry hierarchy. Use no cards, KPI tiles, or decorative fields.
6. Preserve Zammad as the primary ticket workflow.

## Colour

- **Paper** is a warm-neutral ground. It is not parchment and has no texture.
- **Sheet** (white) appears only where the operator edits or compares: inputs, the Changes and
  Review column, and dialogs.
- **Ink** is violet-black, with two lighter steps for secondary text and labels.
- **Copying ink** `#4B3A8A` is the brand mark's colour. It is the aniline violet of official
  annotations and carbon copies. It marks the current route, focus, links, primary actions and
  edited fields, and is never used as a large field or gradient.
- **Ok, caution and fault** are semantic only. Each has a pale wash used behind banners.
- Every text pair meets 4.5:1 in both schemes. Field edges meet 3:1.
- A dark scheme maps the same roles through `prefers-color-scheme`.

## Typography

Atkinson Hyperlegible Next sets headings, controls and text. Atkinson Hyperlegible Mono sets
paths, revisions, request IDs, timestamps and versions only. The family was drawn to keep 0/O,
1/l/I and rn/m apart, which is the failure mode of reading identifiers under pressure. Both fonts
are served from the packaged asset routes with `font-display: swap`. The regular face is preloaded.

Scale: 0.8125 (labels, notations) · 0.9375 (data, controls) · 1 (text) · 1.25 (sections) ·
1.625 (figures, narrow titles) · 2.125rem (page titles). Labels are uppercase with 0.06em
tracking. Numerals are tabular throughout.

## Layout

- **Running head:** wordmark (as wide as the margin track), three routes underlined in copying
  ink, then locale and sign-out. No sidebar.
- **Page head:** the margin holds the refresh time or a back link. The body holds the h1, the
  lede and at most one action.
- **Annal rows:** a top hairline on each row, with section headings hanging in the margin. The
  Overview's live process uses three ruled gauges. The Jobs filters sit in the margin.
  Configuration keeps a sticky group index in the margin and a sticky Changes and Review column
  beside the fields.
- **Below 52rem** the margin folds into the left rule, which keeps running down the page. Margin
  content stacks above its entry.

## Signature details

- **The margin rule as chronology.** On the ticket history, event times sit in the margin and a
  state mark sits on the rule: square for processed, diamond for failed, filled circle for
  running, ring for accepted. An edited configuration field gets a copying-ink bar on the same rule.
- **Notations, not pills.** States are uppercase labels with a leading glyph and a 2px
  underline in the state's colour: square = ok, triangle = caution, diamond = fault, circle =
  live, ring = pending, dash = idle. Status never relies on colour alone.

## Components and states

- Buttons are rectangular and at least 44px high, with direct verb labels. Variants: secondary
  (ink outline), primary (copying ink), quiet (underlined text), danger (fault outline).
  Disabled buttons use the sunk paper, and busy buttons swap their label.
- Inputs have persistent labels and 3:1 edges. Focus uses copying ink. An invalid input gets a
  2px fault edge. An environment-owned (disabled) input gets a dashed edge.
- Banners have a 4px ruled edge on a wash. Feedback stays in context and never relies on a toast.
- Consequential actions (reprocess, restore) are `<details>` disclosures that open a ruled panel
  with an acknowledgement checkbox.
- The review diff strikes the old value and sets the new one in copying ink. In a narrow column it
  becomes labelled entries through a container query.
- Empty states are factual and stay where the data would appear.
- Reauthentication uses a native `<dialog>` with an ink border.
- Motion is limited to 120ms colour transitions and a 160ms opacity reveal of the review.
  Reduced motion removes all of it.

## Source layout

| Path | Role |
| --- | --- |
| `frontend/admin/css/*.css` | Modular styles: fonts, tokens, base, shell, controls, overview, records, config, login, details, motion |
| `frontend/admin.ts` + `frontend/admin/*.ts` | Modular admin TypeScript (bundled) |
| `src/chronikwerk/web/static/admin/` | Assembled CSS, bundled JS, mark, fonts, and font licence |
| `src/chronikwerk/web/templates/admin/` | Server-rendered Jinja templates (`_notation.html` holds the state-to-tone map) |

Build with `make frontend-update`.

## Content and boundaries

Use operator vocabulary and active verbs: Check storage now, Filter, Review changes, Stage
revision, Request reprocessing. Success repeats the action name. Errors state what failed and
the next recoverable step. Volatile, staged, active, and external are not interchangeable.
Do not imply legal certification, durable processing, archive completeness, or storage
health that the current evidence does not establish.

Do not add archive browsing, durable-queue controls, secret management, live reload, service
restart controls, fake metrics, charts, onboarding tours, third-party font services, icon
libraries, animation dependencies, generic cards, or ornamental archive imagery.

The maintained product, audience, interaction, accessibility, and validation contract is in
[`docs/admin-frontend.md`](docs/admin-frontend.md).
