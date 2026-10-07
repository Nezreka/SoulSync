# Visual baselines

**Optional.** These screenshot tests need Docker and are not part of
`npm run check` or `npm test`, so a normal PR doesn't need them. Run them
when you change CSS or layout: they show whether a refactor changed anything
it didn't mean to. CI runs them as their own `webui-visual` job.

They cover the main routes, overlays and states.

```sh
npm run test:visual          # build, then compare every shot with its baseline
npm run test:visual:update   # build, then rewrite the baselines
node scripts/visual.mjs -g settings   # just the tests, any playwright flag
```

The tests run inside the official Playwright Docker image
(`mcr.microsoft.com/playwright:v<@playwright/test version>-jammy`), locally and
in CI, because fonts and text rendering differ between distros and a baseline
only reproduces where it was rendered. `scripts/visual.mjs` starts the
container with `webui/` mounted. You need Docker, but no local browser install.
Running `playwright test -c playwright.visual.config.ts` outside the image
stops with an error. `VISUAL_ON_HOST=1` overrides that for a throwaway probe
spec, but don't compare or update baselines that way.

Both npm scripts run the full `npm run build` first: the app bundle and the
shell bundle (`static/dist/shell.js`). A bare `vite build` empties
`static/dist` and drops `shell.js`, so the page boots without the shell
globals, such as the sidebar "Library / <artist>" breadcrumb. A shot fails
outright when any `/static/` file is missing, so run `npm run build` before
calling `scripts/visual.mjs` on its own.

## Comparing another branch

```sh
git fetch origin
npm run test:visual:compare -- origin/some-branch            # every shot
npm run test:visual:compare -- origin/some-branch -g discover
```

`scripts/visual-compare.mjs` checks the ref out into
`.visual-compare/<ref>` (a git worktree at the repo root, reused on later
runs), copies this checkout's harness and baselines over it, and runs the
shots there. Each shot shows whether the other branch looks different from
this one. The HTML report has expected/actual/diff images per shot; the
script prints the `npx playwright show-report` command to open it.

The default tolerance, pixelmatch at 0.2, is too loose for that: it lets
through colour snaps such as rose `#f43f5e` to red `#ef4444`. Pixelmatch
strict enough to catch those also trips on blur and glow rasterisation that
changes from run to run on the same build. The compare therefore uses
Playwright's perceptual `ssim-cie94` comparator. A pixel counts once its
colour moves past a just-noticeable difference (CIE94 ΔE > 1, about two grey
levels on a flat fill) and it isn't rasterisation noise. It is stable on a
build against its own baselines. `VISUAL_THRESHOLD=<n>` switches back to
pixelmatch at that threshold.

## What's covered

- **Routes** (`ROUTES`): every main page at 1440×900, plus the key ones at
  768×1024 (`MOBILE_ROUTES`).
- **Below the fold** (`FULL_CONTENT`): long pages also get a `<route>-full.png`.
  The app scrolls inside `.main-content`, so the viewport grows until that
  container stops scrolling, and the whole content is shot at once.
  - Scrolled shots were tried and don't repeat.
  - Two discover areas, the adventurousness dial and the map-tools section,
    are masked in the full shot (`FULL_CONTENT_MASKS`); the comment there
    explains why.
- **States** (`STATES`, desktop only): the issue detail modal, a DialogFrame
  modal (podcasts' Add by RSS), the collapsed sidebar, and the issues
  empty/error and stats error states.

## How it works

There is no server. `harness.ts` renders `webui/index.html` the way Flask
would, serves `webui/static` (including the Vite build in `static/dist`) from
disk, and answers every API call.

- `fixtures.ts` holds the API data as MSW `http.*` handlers, the same idiom
  the vitest route tests use. Every route in `ROUTES` that has data gets a
  handler list in `routeHandlers`, and the overlay/error shots use
  `stateHandlers`.
- Anything without a handler gets `{}`, which most pages treat as empty.
- Off-origin requests (cover art, CDNs) are aborted.

Each shot is made repeatable:

- The clock is frozen and `Math.random` is seeded.
- Motion is reduced, the first-launch tip is suppressed, and the test waits
  for the delayed help badge, network quiet and fonts.
- Running CSS animations are cancelled (infinite) or finished (finite) before
  the shot, as the screenshot itself would.

The browser is the image's chromium, pinned by the `@playwright/test` version
in `package-lock.json`.

## When to update the baselines

- **Never in a "no visual change" refactor.** A diff there is a regression, so
  fix the CSS. Look at `test-results/**/<shot>-diff.png` to see what moved (CI
  uploads them as the `visual-diffs` artifact).
- **Only when a change is meant to look different.** Update, eyeball the new
  PNGs in the diff, and call the change out in the PR.
- After a Playwright upgrade, which changes the image and the browser,
  regenerate in a commit of its own. Bump the image tag in
  `.github/workflows/build-and-test.yml` to match.

## Adding a shot

- **A route:** add it to `ROUTES` in `routes.visual.spec.ts`. Add it to
  `MOBILE_ROUTES` for a 768px shot, and to `FULL_CONTENT` if it's long.
- **An overlay or state:** add it to `STATES`. `act` opens it once the page has
  settled; `init` runs before load.
- **Data:** give it handlers in `fixtures.ts`. Keep the data small and fixed:
  no `Date.now`, no randomness, null image urls.

Then run `npm run test:visual:update`, and `npm run test:visual -- --repeat-each 5`
to check the new shot repeats.
