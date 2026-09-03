// Tests for the notification progress-bar maths in `webui/static/downloads.js`.
//
//     node --test tests/static/test_notif_progress.mjs
//
// The pytest wrapper at `tests/test_notif_progress_js.py` surfaces the result
// inside the regular pytest run.
//
// #1197 (wishx): "Library maintenance" showed 100% while the counts under it
// read 2,347 / 157,122 — one and a half percent. Minutes later, at 2,360, it
// showed the correct 2%. The trigger looked like other automations finishing;
// it was actually the scan crossing 1% -> 2%. _taskClampPct treated any value
// <= 1 as a 0-1 fraction and multiplied it by 100, so an honest integer 1
// percent rendered as 100. Every long-running job passes through exactly 1%.

import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DOWNLOADS_PATH = resolve(__dirname, '..', '..', 'webui', 'static', 'downloads.js');

let sandbox;
before(() => {
    const source = readFileSync(DOWNLOADS_PATH, 'utf8');
    // Only the pure percent helpers are under test; the file is huge and full
    // of DOM wiring, so lift the three functions rather than execute it all.
    const wanted = ['_taskClampPct', '_taskHasPct', '_taskPct', '_taskPctText'];
    const picked = wanted.map((name) => {
        const start = source.indexOf(`function ${name}(`);
        assert.ok(start !== -1, `${name} not found in downloads.js`);
        // walk braces to the end of the function
        let i = source.indexOf('{', start);
        let depth = 0;
        for (; i < source.length; i++) {
            if (source[i] === '{') depth++;
            else if (source[i] === '}') { depth--; if (depth === 0) { i++; break; } }
        }
        return source.slice(start, i);
    }).join('\n');
    sandbox = {};
    vm.createContext(sandbox);
    vm.runInContext(picked, sandbox);
});

describe('_taskClampPct', () => {
    test('an honest 1% is one percent, not one hundred (#1197)', () => {
        // wishx's exact frame: 2,347 of 157,122 rounds to 1
        const progress = Math.round((2347 / 157122) * 100);
        assert.equal(progress, 1);
        assert.equal(sandbox._taskClampPct(progress), 1);
        assert.equal(sandbox._taskPctText(progress), '1%');
    });

    test('the next frame he screenshotted still reads 2%', () => {
        const progress = Math.round((2360 / 157122) * 100);
        assert.equal(progress, 2);
        assert.equal(sandbox._taskClampPct(progress), 2);
    });

    test('no whole percent between 1 and 100 is ever inflated', () => {
        for (let p = 0; p <= 100; p++) {
            assert.equal(sandbox._taskClampPct(p), p, `${p}% must render as ${p}%`);
        }
    });

    test('a genuine 0-1 fraction is still scaled', () => {
        assert.equal(sandbox._taskClampPct(0.42), 42);
        assert.equal(sandbox._taskClampPct(0.075), 8);   // rounds
    });

    test('out-of-range and junk values stay in bounds', () => {
        assert.equal(sandbox._taskClampPct(140), 100);
        assert.equal(sandbox._taskClampPct(-5), 0);
        assert.equal(sandbox._taskClampPct('nonsense'), 0);
        // the fallback covers NaN-ish input only: Number(null) is 0, which is
        // finite, so null clamps to 0 rather than reaching the fallback. every
        // caller gates on _taskHasPct first, which rejects null outright.
        assert.equal(sandbox._taskClampPct(undefined, 7), 7);
        assert.equal(sandbox._taskClampPct(null, 7), 0);
        assert.equal(sandbox._taskHasPct(null), false);
    });
});

describe('_taskPct', () => {
    test('a long scan early on reports single digits, not completion', () => {
        assert.equal(sandbox._taskPct(2347, 157122), 1);
        assert.equal(sandbox._taskPct(1, 157122), 0);
        assert.equal(sandbox._taskPct(157122, 157122), 100);
    });

    test('no total means indeterminate unless the phase says done', () => {
        assert.equal(sandbox._taskPct(5, 0, 'running'), null);
        assert.equal(sandbox._taskPct(5, 0, 'finished'), 100);
    });
});
