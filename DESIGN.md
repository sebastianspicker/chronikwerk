# Design System

## Product register

This is a restrained, light operations product. Design serves fast scanning, safe
action, and truthful state. It is not a marketing surface or a generic dashboard.

## Theme and color

Use the following semantic tokens for the admin application:

| Role | Value | Use |
| --- | --- | --- |
| Canvas | `#f6f8fb` | Application background |
| Surface | `#ffffff` | Main content and overlays |
| Text | `#172033` | Primary text |
| Muted text | `#526176` | Secondary explanations |
| Border | `#cbd5e1` | Grouping and control boundaries |
| Accent | `#0b57d0` | Primary actions, focus, current location |
| Accent hover | `#0847ad` | Hover and active accent state |
| Success | `#166534` | Success text with a pale matching background |
| Warning | `#854d0e` | Warning text with a pale matching background |
| Error | `#991b1b` | Error text with a pale matching background |

Status always includes text and never relies on color alone. One-pixel borders provide
grouping. Shadows are reserved for true overlays. Gradients, glass effects, decorative
backgrounds, dark mode, and decorative motion are out of scope.

## Typography

Admin UI uses the operating system UI sans stack at 14–15px. Headings use a fixed
16/18/24px scale. Numeric operational data uses tabular numerals, and monospace is
limited to identifiers and hashes. Prose is capped at 75 characters per line.

PDF output uses the production-guaranteed DejaVu Sans family, 10.5pt body text with a
1.45 line height, 11.5pt article headings, and a 16pt ticket title. Technical content
wraps rather than clipping.

## Spacing and shape

Spacing uses 4, 8, 12, 16, 24, and 32px increments. Radii use 4, 6, and 8px. Controls
are at least 40px high. Focus uses a 3px blue outline with a 2px offset. The admin shell
uses a sidebar at desktop widths and compact top navigation below 1024px.

## Components and patterns

- Use a skip link, landmarks, one page heading, breadcrumbs, and explicit current
  navigation state.
- Use native labeled fields, buttons, links, checkboxes, and select controls.
- Buttons have consistent primary, secondary, danger, disabled, loading, hover, active,
  and focus-visible states.
- Persistent inline banners carry success, warning, and error feedback and use
  `aria-live` where asynchronous changes occur.
- Tables remain semantic tables inside labeled, keyboard-focusable scroll regions.
- Empty, loading, stale, error, and session-expiry states explain recovery.
- Consequential retry, security, staging, and rollback actions use inline disclosure and
  specific acknowledgement rather than a generic modal.

## Responsive behavior

Desktop and laptop are primary. The application is complete at 768px, safe at 390px,
320px, and 400% zoom, and does not hide critical columns without an equivalent labeled
detail. Tables scroll horizontally in a focusable region. Layout changes structurally;
typography does not use fluid display scaling.

## Accessibility and performance

Use semantic HTML before ARIA, preserve logical focus order, provide visible focus, and
pause background refresh while the document is hidden. Honor `prefers-reduced-motion`.
No browser asset may require an external request. Initial HTML must remain at or below
100KB and combined uncompressed CSS and JavaScript at or below 150KB. Render at most 50
job rows per page.

## Archive document structure

PDFs follow ticket title, ticket metadata, chronological articles, attachment inventory,
and page footer. Headings form a meaningful outline. Page identity includes ticket ID and
page numbering. Total, included, and omitted article counts are explicit. Tables, lists,
code, long URLs, attachment names, and multi-page articles remain within A4 bounds.
