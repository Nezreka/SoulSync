# Styling the web UI

How CSS is written in `webui/`, and what `npm run check` and `npm test` hold it to.

There are two kinds of CSS:

- **CSS modules** (`webui/src/**/*.module.css`), imported by React components. All new UI goes here.
- **Legacy sheets** (`webui/static/*.css`), linked from `webui/index.html` and styled by class names the vanilla JS builds from strings. Change them by editing the rule that already exists.

## Tokens

`webui/static/tokens.css` is the one global `:root`, and every sheet and module can read it. It holds the accent, the text and surface colours, the `--white-aNN` / `--black-aNN` alpha ramps, status colours, radii (`--radius-*`), z-index layers (`--z-*`), durations and easings. Read it for the names and values. Each colour comes with an `-rgb` triplet for alpha: `rgba(var(--accent-rgb), 0.3)`.

When no token fits, add one to `tokens.css` and use it, rather than writing the literal.

## Breakpoints

The list is in the header comment of `tokens.css` (480, 640, 768, 1024, 1280px). `@media` can't read custom properties, so write the pixel value. `max-width: 768px` is the switch to the mobile layout. Reuse one of these values; the budget test counts distinct breakpoints.

## CSS modules

- Classes are camelCase (`.trackRow`). Variants are `data-*` attributes on the element (`&[data-size='sm']`, `&[data-tone='danger']`), not extra classes.
- Colours come from tokens: no hex, no literal `rgb()`/`hsl()`. `z-index` is a `var(--z-*)` (or a single digit for layering inside a component). `border-radius` is a `var(--radius-*)`. No `!important`: raise specificity or fix the rule you're fighting.
- Every class must be read by a component that imports the module (`styles.x` or `styles['x']`). A class picked at runtime (`styles[`badge_${kind}`]`) goes in the `DYNAMIC` list in `webui/src/test/css-module-classes.test.ts`, with the lookup that uses it.
- Colours stay in CSS. Pass a runtime value through a custom property (`style={{ '--progress': pct }}`) instead of an inline `style={{ color }}`.

Use the shared components before writing new CSS for the same thing:

| Need | Component |
|---|---|
| Buttons, inputs, selects, checkboxes, switches | `Button`, `TextInput`, `Select`, … in `@/components/form/form` |
| Pills and status labels | `Badge` in `@/components/primitives/primitives` |
| Inline messages | `Notice` (same file) |
| Loading | `Spinner`, `Skeleton` (same file) |
| Nothing to show | `EmptyState` (same file) |
| Modals | `DialogFrame`, `DialogHeader`, `DialogBody`, `DialogFooter` in `@/components/dialog/dialog` |

## Legacy sheets

Find the rule that styles the thing and change it there. Overrides appended later (a `v2`, `premier` or `overhaul` sheet, or a new section at the end of a file) are how these sheets grew to tens of thousands of lines, and the budget test fails on a new `<link>` in `index.html`.

stylelint is lenient here: only rules the sheets already pass are on. A few existing exceptions carry a `/* stylelint-disable-next-line <rule> -- <reason> */` comment. Those mark old code that can't change without changing the page; fix new code instead of adding one.

## Checks

From `webui/`:

```sh
npm run check   # oxfmt, oxlint and stylelint (npm run lint:css on its own)
npm test        # includes the CSS budget and the unused-module-class test
npm run fix     # autofix what the linters can
```

- **stylelint** (`webui/.stylelintrc.json`). Modules that broke a strict rule before it existed are listed per rule near the end of the file. When you fix one of those modules, remove it from the list. A new module gets every rule.
- **CSS budget** (`webui/src/test/css-budget.test.ts`, numbers in `css-budget.json`). Counts hex colours, literal `rgb()`, `!important`, repeated selectors, distinct `z-index` values and breakpoints, duplicate `@keyframes` and custom properties used but never defined. A count may only go down. When yours goes down, the test prints a command that lowers the baseline: `UPDATE_CSS_BUDGET=1 npx vitest run src/test/css-budget.test.ts`. Commit the updated `css-budget.json`.
- **Screenshots** (optional, needs Docker): `npm run test:visual`. Worth running for CSS or layout changes. See `webui/tests/visual/README.md`.
