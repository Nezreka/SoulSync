# Help/Docs page port — working notes

Port of `/help` (legacy `webui/static/docs.js`, ~3.5k lines) to the React
shell, following the #1018 protocol: the characterization spec
(`webui/tests/pages/help.spec.ts`, merged in #1016) passes against the legacy
page first, and the SAME spec must pass unchanged after the manifest flip.

Baseline confirmed 2026-08-08: 8/8 (desktop + mobile) against legacy on dev
@ 41a57f62.

## Legacy anatomy

- `DOCS_SECTIONS` — the whole document as data: `{id, title, icon,
  children[], content: () => html}`. Content is trusted, in-repo authored
  HTML strings. The API-reference section builds its HTML programmatically
  (endpoint tables + interactive "Try It" forms).
- `initializeDocsPage()` — renders nav + all sections up front, wires the
  filter box (hides non-matching sections; no re-query), scroll-spy on the
  content pane, collapsible nav sections, deep links (`/help#<child-id>`).
- `docsImg()`/lightbox — screenshot embeds with a click-to-zoom overlay.
- **App-wide contract:** `navigateToDocsSection(sectionId)` — called by
  toasts ("Learn more →", downloads.js) and helper-mode tours. Must keep
  working from vanilla callers after the flip.

## Port plan

- P1 `-help.sections.ts` — DOCS_SECTIONS ported as data; content stays HTML
  strings rendered via `dangerouslySetInnerHTML` (same trust model as the
  legacy `innerHTML`; authored in-repo, no user input). Class names + ids
  preserved byte-for-byte so the spec and style.css keep working.
- P2 `-help.api-reference.ts` — the programmatic API section builder ported;
  "Try It" forms become React components.
- P3 `-help.helpers.ts` — pure filter/match logic + vitest.
- P4 `-ui/help-page.tsx` — sidebar nav, filter box, scroll-spy, lightbox.
- P5 bridge — `window.navigateToDocsSection` shim that routes into React
  (matches the severs pattern from the dashboard/tools flips).
- P6 THE FLIP — route-manifest `help: react`, delete the legacy markup
  region + `docs.js` script include, sever leftovers, same spec passes.

One page, one PR (draft from the start), screenshots at 1440 + 375.
