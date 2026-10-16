# Issues filter cleanup evidence

Part of #1727. The production change is confined to the Issues page: replace three repeated URL-update handlers with one typed handler. Filter values, URL defaults, history replacement, entity clearing, markup, copy, CSS and backend behavior are unchanged.

## Before and after

Before is unmodified `dev` at `2b460c7e9`, rendered with this PR's test harness by `scripts/visual-compare.mjs`. After is the refactored build. Both use the pinned Playwright 1.59.1 Docker image, fixed fixtures, UTC, reduced motion and the same clock. The paired images retain the original screenshot pixels; only the labels above them are added.

| Viewport | Default filters: changed pixels | Combined filters: changed pixels |
| --- | ---: | ---: |
| Desktop, 1440 x 900 | 0 | 0 |
| Narrow, 768 x 1024 | 0 | 0 |

Counts above are exact RGB comparisons of the saved before/after screenshots, not just the suite's thresholded comparison. The new four baselines were captured before changing production code and were not refreshed afterward. Repeated comparisons also pass at the existing suite tolerance.

Combined filters are In Progress / Audio Quality / Tracks:

- [Desktop before and after](./desktop-before-after.png)
- [Narrow before and after](./mobile-before-after.png)

The four reusable baselines are in [`webui/tests/visual/__screenshots__`](../../../webui/tests/visual/__screenshots__); their names start with `issues-filters-` under `desktop/` and `mobile/`.

## Behavioral coverage

- Four route-test cases verify individual filter changes preserve the other search values, clear the entity parameter for All items, and replace history.
- Browser tests at both widths combine all three filters, check the URL, selected options, API query parameters and matching fixture row, then clear each filter independently.
- Back returns to Stats rather than an intermediate filter state; Forward restores the final filters.
- Opening issue #7 and closing its dialog retain the filters.
- Negative controls: temporarily dropping the existing search values caused all four route cases to fail; temporarily changing `replace: true` to `false` also caused all four to fail. Neither mutation is retained.

The browser harness serves the real built frontend with mocked APIs. This does not validate a running Flask backend, a real database, external services, or browsers other than Chromium.

## Local checks

From `webui/`:

| Command | Result |
| --- | --- |
| `npm run check` | Pass: 0 errors; 538 existing warnings, same as the original baseline |
| `npm run build` | Pass |
| `npm test -- src/routes/issues` | 55 tests pass across 5 files |
| `node scripts/visual.mjs issues-filters --repeat-each=5 --workers=2` | 10 browser runs pass, checking all four screenshots five times |
| `node scripts/visual-compare.mjs upstream/dev issues-filters --workers=2` | Both runs pass on unmodified `dev` |
| `npm test -- --maxWorkers=4` | 10,475 pass; 4 unrelated failures reproduced on unmodified `dev` |
| `node scripts/visual.mjs -g 'issues\|issue-detail-modal' --workers=2` | New flows and existing detail shot pass; 3 existing shots have the same stale-baseline differences before and after |

Local Node is 26.8.1. Browser renders use the repository's pinned Docker image. Backend tests and live-server E2E tests were not run because this changes neither backend code nor API contracts. GitHub CI status is reported separately in the PR.

### Existing base failures

Running `npm test -- src/test/export-coverage.test.ts src/test/vanilla-crossfile.test.ts` in the unmodified `upstream/dev` comparison worktree reproduces the same four failures:

- Export coverage rejects four newly added Beatport exports that no test names.
- Cross-file inventory still expects five teardown functions from the deleted `static/beatport-ui.js`.
- The settings-picker check tries to read that deleted file.
- The Beatport teardown assertion expects those removed edges.

Existing `desktop/issues`, `desktop/issues-empty` and `desktop/issues-error` screenshots each differ from their committed baselines by 502 pixels in the sidebar's old Downloads layout. Their actual before/after images are pixel-identical. Those unrelated baselines were not rewritten to make this no-visual-change refactor pass.

## Scope and review order

1. Review the characterization tests and new screenshots.
2. Review `webui/src/routes/issues/-ui/issues-page.tsx`: one handler now merges a typed partial filter update into the previous search state with `replace: true`.
3. Inspect the paired images and the recorded base failures above.

No changes to the CSS program in #1578/#1702, the library overhaul's issue-detail modal, shared components, test-policy guardrails, persistence, or backend behavior are included.
