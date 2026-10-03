# Design brief: Chronikwerk administration

Status: brief and direction are final. The build follows the chosen direction ("Annalen").
Date: 2026-10-03.

## 1. Product summary

Chronikwerk is a self-hosted Python service. It takes authenticated Zammad webhook events
and, for each ticket, writes an archival PDF (PDF/UA target, German or English) with a JSON
audit sidecar. It can add a PAdES signature and an RFC 3161 timestamp. It then reports the
outcome back to Zammad as tags and an internal note. Archiving runs headless. The only
graphical surface is an optional, feature-flagged **administration application**. It is
server-rendered with Jinja, uses dependency-free TypeScript, and is shipped as the packaged
assets `admin.css`, `admin.js` and the brand mark. A static mock of it runs as a GitHub
Pages demo (`demo/site/`).

Admin surfaces (all in scope):

| Route | Purpose |
| --- | --- |
| `/admin/login` | Token sign-in (an externally managed secret) |
| `/admin` Overview | Process liveness, storage probe, admission capacity, recent failures, active and staged revision, version, signing and timestamp state |
| `/admin/jobs` | Volatile job-event history with ticket and status filters and cursor paging |
| `/admin/jobs/{id}` | Per-ticket event timeline plus an acknowledged "reprocess" action |
| `/admin/configuration` | Allowlisted non-secret settings in groups, with provenance, review diff, security acknowledgement, and staging with optimistic concurrency |
| `/admin/configuration/revisions` | Revision history and acknowledged restore |
| Reauth dialog | Inline token re-entry when the session expires mid-edit |

**Moment of value.** An operator opens the console during an incident. They need to know
within seconds: is the process alive, is storage writable, is work piling up, what failed
and why, and is a staged configuration waiting for a restart. A second moment comes when a
failed ticket is reprocessed with full awareness that the PDF and sidecar may be overwritten.

## 2. Audience

**Primary: the operator.** This is a DevOps or Zammad administrator in a German-speaking
institution: a school, university, public body or mid-size organisation that runs Zammad
on-premises. They are technically fluent, read YAML and logs, and use a terminal, Zammad,
Grafana or Prometheus, a ticket queue and a password manager every day. They come to the
console rarely and mostly under pressure (after a failure, during a deployment).

- **Goals:** confirm health quickly; find the ticket; understand the classification
  (PERMANENT, TRANSIENT and so on); reprocess safely; change a limit without SSH.
- **Anxieties:** overwriting archives; thinking work is done when it was only *accepted*
  (`202`); changes that silently take effect, or silently do not; leaking secrets;
  looking negligent in an audit.
- **Distrusts:** marketing gloss, invented metrics, green checkmarks that mean nothing,
  toasts that disappear, anything that hides IDs.
- **Quality signals:** exact identifiers that copy and paste cleanly; legible hashes
  (no 0/O or 1/l confusion); a stable layout; honest wording about volatility; keyboard
  operation; correct German.

**Secondary: the compliance or security reviewer.** Reads revision history and states.
Needs non-colour status, readable identifiers and claims that are no stronger than the
evidence. Help-desk staff never see this UI.

## 3. Key journeys (in priority order)

1. **Incident triage:** Overview → recent failure → ticket timeline → (optional) reprocess.
2. **Health glance:** Overview, read in three seconds (alive, storage, load, staged revision).
3. **Configuration change:** Configuration → edit → review the diff → acknowledge → stage →
   external restart.
4. **History search:** Jobs → filter by ticket or status → page back.
5. **Audit and rollback:** Revisions → inspect → restore as a new staged revision.
6. **Session boundary:** sign in, inline reauthentication, locale switch, sign out.

## 4. Brand traits

| Trait | …not tipping into |
| --- | --- |
| **Exact**: every identifier, state and timestamp is shown precisely | pedantic, code-dump, raw JSON |
| **Calm under load**: the same composed layout in an incident | sleepy, low-contrast, hiding urgency |
| **Accountable**: actions name their consequence, states name their boundary | bureaucratic, fearful, wall-of-warnings |
| **Institutional**: at home in a German public-sector IT department | stuffy, nostalgic, seal-and-parchment |
| **Quietly authored**: a few distinctive decisions, held consistently | quirky, decorative, branded for its own sake |

## 5. Market observations

Category references: Zammad's own admin (light grey, green accent, card lists); queue and
ops consoles (Sidekiq Web, RabbitMQ management, Celery Flower, Grafana); document and
archive products (DocuWare, MailStore, ELO); generic SaaS admin templates.

Conventions **honoured** because users depend on them:
- persistent primary navigation with three stable areas;
- true data tables for events and revisions, with sortable-looking but plain headers;
- red, amber and green semantics for failure, warning and ok, here always paired with shape and text;
- native form controls, labels above or beside fields, explicit submit buttons.

Conventions **broken** because they make everything look alike:
- the dark left sidebar with a filled-blue active pill (also the current UI);
- KPI tiles with giant numbers and progress bars as "dashboard decoration";
- every section as an identical white rounded card with a shadow;
- system-font or Inter typography with no point of view;
- status pills as the only status language.

## 6. What to keep

- **The mark** (folio plus timeline dots, violet ink `#4B3A8A`). `docs/assets/brand/README.md`
  forbids recolouring it. The current UI ignores its colour (navy and process blue), which
  is an incoherence. The redesign takes its colour from the mark.
- The three-route information architecture and every route, form field, `data-*` hook and
  i18n key. The TypeScript reads these.
- The content principles in `PRODUCT.md`: volatile, staged, active and external are distinct;
  no fake metrics; consequences stated before actions.
- Native semantics: definition lists for facts, tables for records, an ordered list for the
  ticket timeline, native `<dialog>`, `<details>` for consequential disclosures.

## 7. Current weaknesses (rendered review of the "Register" UI)

- It reads as a generic SaaS admin: a dark sidebar with a blue pill, white cards, and big blue numbers.
- The spine does not reach the bottom of long pages (Configuration at 1440 px ends after about 650 px).
- The overview hierarchy is flat. Storage, Running and Pending have equal weight, and the
  "Process alive" signal sits in a corner in small green text.
- Configuration is a 3,000 px stack of boxed rows. The label, path and provenance compete;
  the review column floats empty and disconnected beside the first group.
- Monospace (`SFMono` / Consolas / Liberation) and the sans (`Avenir Next` / Segoe UI)
  change across platforms. Linux operators get unrelated fallbacks.
- The brand mark's violet appears nowhere else in the UI.
- At mobile width the navigation is a heavy two-row navy block. The timeline is cramped.

## 8. Constraints (load-bearing)

- Routes, form actions, field names, CSRF, `data-*` hooks used by `frontend/admin/*.ts`,
  `data-signing-state` / `data-timestamp-state` markup (pinned by contract tests), page
  titles and h1 text (pinned by browser tests), and the i18n key parity between `de-DE` and `en-GB`.
- The CSP is `default-src 'self'`, so there are no external fonts, CDNs or inline styles. Assets
  are served from fixed, cached routes (`test_admin_route_table.py`, `test_admin_assets.py`).
- `admin.css` and `admin.js` are generated (`make frontend-update`) and checked for staleness.
- The demo (`demo/site/`) reuses the packaged `admin.css` and must stay network-free.
- WCAG 2.2 AA, a 42 px minimum control height (product contract), 320 px reflow, 400 % zoom,
  reduced motion, and non-colour status.
- `make verify-core` must pass. Browser tests run at 1440, 390 and 320 px.

## 9. Assumptions log

| # | Assumption | Evidence | Confidence |
| --- | --- | --- | --- |
| A1 | Primary users are German-speaking institutional IT operators | `de-DE` is the default locale, the name and German labels, Zammad on-prem focus, PAdES and "Datacenter" signing defaults | high |
| A2 | The console is used rarely and under pressure, mostly on desktop | process-local scope, retry and failure focus, PRODUCT.md "deployment … incident response" | high |
| A3 | Self-hosting two OFL fonts (~52 KB) is acceptable despite the old "no external fonts" rule | the rule targets *external* (CDN) fonts, which CSP forbids. Self-hosted fonts are same-origin and give platform-stable identifier legibility | medium |
| A4 | Violet ink as the action colour is welcome rather than off-brand | the mark is violet and its colour must not change; the UI never used it | medium |
| A5 | A dark scheme is useful (operators at night, terminals in dark mode) | no direct evidence; common in the audience's tools | low (implemented token-only, so it is cheap to drop) |
| A6 | Classification values are a small fixed set (SUCCESS, TRANSIENT, PERMANENT, …) | `class-tag--{{classification|lower}}` in templates, failure policy in `archiving/` | high |
| A7 | Ticket IDs are short integers and revision IDs are 64-hex digests | templates truncate revisions `[:16]…[-6:]`, fixture `4815` | high |

---

## Design direction

The concept had to come out of the product itself. The name **Chronik** means chronicle,
and **Werk** means works. The mark is a **folio with a timeline of dots in violet ink**. The
work is making the **record copy** of a conversation. The emotional state is that of someone
who needs to know, now, what is on record.

### Direction 1: "Schriftfeld" (title block)

- **Concept:** the title block and revision index of a technical drawing (DIN EN ISO 7200).
  Each page opens with a ruled grid of labelled cells: version, active revision, staged
  revision, process started. Revisions read as an *Änderungsindex*. This fits because
  configuration revisions *are* drawing revisions, and engineers trust title blocks.
- **Type:** Barlow Semi Condensed (labels, DIN-like) with IBM Plex Mono (values), at a tight
  scale from 11 to 28 px.
- **Colour:** graphite on white; one revision-cloud red for staged or changed; semantic green and amber.
- **Layout:** a hairline cell grid everywhere, dense, with a 4 px rhythm.
- **Motion:** none beyond focus.
- **Signature:** the title-block header; changed fields drawn with a "revision triangle" marker.
- **Stands apart by:** an engineering-document grid instead of a dashboard.
- **Refuses:** cards, big numbers, pills.
- **Risk:** cell grids become boxes on boxes, and the semantic red clashes with "staged". It reads
  as CAD software and is cold for a Zammad admin. Plex is a common developer-tool font.

### Direction 2: "Annalen" (margin chronology), CHOSEN

- **Concept:** medieval annals and modern registry files share one structure. The **margin
  carries time and reference** (year, date, file reference, *Vermerk*), and the **body carries
  the entry**. Chronikwerk's job is that structure: a time, an identifier and an outcome. Every
  page is set as an annal. A narrow, ruled **margin column** holds section names, timestamps
  and state notations. The **body column** holds the record. One violet "copying-ink" colour,
  taken from the mark (the aniline *Tintenstift* used for official annotations and carbon
  copies), marks what is of record and what the operator can act on.
- **Typography:** **Atkinson Hyperlegible Next** (UI, headings, body) with **Atkinson
  Hyperlegible Mono** (identifiers, timestamps, paths), both OFL and self-hosted as variable
  latin subsets. The pairing logic: one family, two voices. Atkinson was drawn by the Braille
  Institute to keep characters such as 0/O, 1/l/I and rn/m apart, which is exactly the
  failure mode of reading ticket IDs, request IDs and SHA revisions under stress. Its slightly
  unconventional, wide-aperture forms give the UI a voice no system font has. Scale (rem at a
  16 px root): 0.75 margin labels in small caps tracking · 0.875 data · 1 body · 1.25 section ·
  2 page title. Weights: 400, 500 and 700 only.
- **Colour (light):** paper `#F8F7F4`, a warm-neutral off-white chosen against "cool SaaS ice"
  and not parchment; surface `#FFFFFF`; ink `#1E1B24`, a violet-black; secondary ink `#5E5866`;
  rule `#DAD6DE`; **copying ink `#4B3A8A`** for primary actions, current route, focus and links;
  semantic fault `#A3222F`, caution `#7A5300` and ok `#1D6B45`, each with a pale wash used
  only behind banners. Dark scheme: ink-black paper `#16141A`, ink `#ECE9F0`, copying ink lifted
  to `#B4A6F0`.
- **Layout:** a 12-column body capped at 72 rem. On desktop, every section is a two-track
  annal row: **margin 13 rem** (sticky section label, counts, notes) and **body** (content).
  Rows are divided by full-width hairlines, not boxes. No cards: the paper is the surface,
  and white surfaces are reserved for things the operator edits or compares (inputs, the
  review diff, dialogs). Navigation is a **horizontal running head** (*Kolumnentitel*) across
  the top: wordmark, three routes underlined in copying ink, then locale and sign-out. This
  replaces the sidebar. At 52 rem and below, the margin label stacks above its entry as a
  small-caps line and the running head wraps into two rows. Mobile is designed as a reading
  column with a sticky compact head and full-width 44 px controls.
- **Motion:** 120 ms colour and border transitions on controls, and a 160 ms opacity reveal of
  the review panel. Nothing moves position; `prefers-reduced-motion` removes all of it.
- **Signature details:**
  1. **The margin rule.** One continuous vertical hairline runs down every page between margin
     and body. On the ticket timeline it *becomes* the timeline: event times sit in the margin
     and state notations sit on the rule as small ink marks (square = processed, diamond = failed,
     circle = accepted or running, ring = skipped). The mark's dotted spine is generalised into
     the whole UI's structure.
  2. **Ink notations instead of pills.** States are set as small-caps text with a leading glyph
     and a 2 px underline in the state's colour, like a clerk's underscored *Vermerk*, so
     status is never a coloured blob.
- **Stands apart by:** no sidebar, no cards, no KPI tiles. It is a typographic ledger with a
  margin chronology, unlike Zammad's green cards or Grafana's panels.
- **Refuses:** gradients, shadows on content, rounded "cards", giant numbers, icons libraries,
  parchment or texture, seals, motion beyond state feedback.
- **Exception noted:** the anti-pattern list warns against purple. Here a single flat violet
  ink is used, inherited from the existing mark that must not be recoloured. It never appears as
  a gradient, glow or large field.

### Direction 3: "Stellwerk" (signal box)

- **Concept:** a railway interlocking panel. The archive pipeline (admitted → running →
  rendered → published → tagged → noted) is drawn as a track diagram with lit sections, and the
  overview is a control desk.
- **Type:** Overpass (Highway Gothic) with Overpass Mono.
- **Colour:** a dark slate panel with signal yellow, green and red lamps.
- **Layout:** a full-bleed diagram at the top, dense tables below.
- **Motion:** lamps fade between states.
- **Signature:** the pipeline track diagram.
- **Stands apart by:** a schematic instead of tiles.
- **Refuses:** light marketing look.
- **Risk:** the diagram would show stages the process cannot truthfully report per job (history
  records only accepted, running, processed, failed and skipped). It invites "glowing status
  indicators" (an explicit anti-reference) and decorative motion. Dark-first hurts compliance
  readers and print.

### Choice

**Annalen** wins on every brief criterion:
- *Specificity:* it is built from the product's name, its mark and its data shape (time,
  reference, outcome).
- *Audience fit:* German registry and annal conventions are familiar without being nostalgic,
  and Atkinson directly serves identifier legibility.
- *Honesty:* it draws nothing the backend cannot evidence.
- *Restraint:* it is one colour and one family.

Schriftfeld was the runner-up. Its idea of a fact header survives as the Overview's opening
"record line". The trade-off is that Annalen is less dense than Schriftfeld and less immediately
"operational-looking" than Stellwerk. Operators who expect a dashboard get a ledger instead, so
the Overview must earn its three-second readability through type hierarchy alone.
