# `_esc` / `_escAttr` are declared twice — the later script silently wins

Found 2026-07-27 while mapping `stats-automations.js` for the artist-detail
port. **Pre-existing, not caused by the React migration.** Not fixed: changing
it shifts rendering app-wide, and the current work is under a 1:1 parity
instruction.

## What happens

`index.html` loads, in order: `core.js` → `library.js` → `stats-automations.js`
→ `init.js`. They share one global scope, so a later `function foo()` overwrites
an earlier one with no error.

| name | declared in | which one actually runs |
|---|---|---|
| `_esc` | `library.js:2464` **and** `stats-automations.js:5764` | stats-automations |
| `_escAttr` | `downloads.js:5187` **and** `stats-automations.js:5771` | stats-automations |

## Why it matters — they are NOT equivalent

```js
// library.js — dead code, never executes
function _esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// stats-automations.js — this is what every caller gets
function _esc(str) { if (!str) return ''; /* ...same as above... */ }
```

The `if (!str) return ''` guard is the whole difference, and it is falsy-based:

- `_esc(0)` → `''` instead of `'0'`
- `_esc(false)` → `''`
- `_esc('')` → `''` (fine, same either way)

So any **zero** value passed through `_esc` renders as blank rather than "0" —
track number 0, disc 0, a 0 play count, a 0 year. ~245 call sites across 7
files go through this.

`_escAttr` is worse, because the two implementations differ in *purpose*:

```js
// downloads.js  — escapes for a JS string literal (backslashes the quote)
function _escAttr(s) { return _escToast(s).replace(/'/g, "\\'")... }

// stats-automations.js — HTML-escapes instead (&#39;)
function _escAttr(str) { if (!str) return ''; return String(str).replace(/&/g,'&amp;')... }
```

`downloads.js` calls `_escAttr` 14 times expecting the backslash behaviour and
silently gets the HTML-entity one. The comment above the stats-automations
version describes the exact bug this causes ("Road trip-The Rolfe's"
delete-button SyntaxError) — so the two files have contradictory fixes for the
same class of problem, and only one of them is in effect.

## Why no test caught it

Both are `function` declarations, so redeclaration is legal — no throw, no
syntax error. `tests/test_vanilla_globals_resolve.py` sees the name resolve
fine; it checks that names EXIST, not that they resolve to the one the author
meant.

## If fixing later

1. Decide the canonical `_esc` (the falsy guard is almost certainly wrong —
   `String(s ?? '')` is the intent).
2. Decide whether `_escAttr` should be the JS-literal escaper or the HTML one,
   then rename the loser: they do different jobs and both are needed.
3. Delete the shadowed copies and re-run the full suite — ~245 call sites means
   real render diffs, especially anywhere a legitimate 0 is displayed.
4. Consider a lint rule for duplicate top-level declarations across the bundle;
   the globals test could be extended to flag redeclarations, not just
   unresolved names.
