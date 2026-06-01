// Tests for the inline-onclick string escaping in `webui/static/stats-automations.js`.
//
// Run via:
//     node --test tests/static/test_stats_automations_esc.mjs
//
// The pytest wrapper at `tests/test_stats_automations_esc_js.py` shells out to
// `node --test` so this fails the suite if the escaping regresses.
//
// Regression context — the "Road trip-The Rolfe's" delete bug:
// A mirrored playlist whose name contains an apostrophe rendered
//     onclick="...deleteMirroredPlaylist(2, 'Road trip-The Rolfe&#39;s')"
// The browser HTML-decodes &#39; back to ' BEFORE the JS parser runs, producing
//     deleteMirroredPlaylist(2, 'Road trip-The Rolfe's')   // unterminated string
// so the whole handler threw a SyntaxError and never executed. Two visible
// symptoms: (1) event.stopPropagation() never ran, so clicking the ✕ bubbled to
// the card and opened the track preview instead; (2) the preview's "Delete
// Mirror" button silently did nothing (no DELETE request). `_escJs` fixes it by
// backslash-escaping the JS metacharacters first; `_escAttr` (HTML-only) cannot.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
    resolve(__dirname, '..', '..', 'webui', 'static', 'stats-automations.js'),
    'utf8',
);

// Pull a top-level `function NAME(...) { ... }` out of the real source by
// brace-matching and return it as a live function, so the tests exercise the
// shipped implementation rather than a copy.
function extractFn(name) {
    const at = SRC.indexOf(`function ${name}(`);
    assert.notEqual(at, -1, `function ${name} must exist in stats-automations.js`);
    const open = SRC.indexOf('{', at);
    let depth = 0;
    for (let i = open; i < SRC.length; i++) {
        if (SRC[i] === '{') depth++;
        else if (SRC[i] === '}' && --depth === 0) {
            // eslint-disable-next-line no-eval
            return eval(`(${SRC.slice(at, i + 1)})`); // named function expression -> returns the fn
        }
    }
    throw new Error(`could not brace-match function ${name}`);
}

const _escJs = extractFn('_escJs');
const _escAttr = extractFn('_escAttr');

// Reproduce what a browser does to  onclick="fn('<value>')"  : the HTML parser
// resolves entities, THEN the JS engine parses the resulting source. &amp; is
// resolved last so already-decoded entities aren't double-processed.
function htmlAttrDecode(s) {
    return s
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&');
}

// Build the exact onclick shape the cards build, decode it like the browser
// would, compile + run it, and capture the argument the handler received.
function invokeOnclick(escFn, name) {
    const onclick = `cb(2, '${escFn(name)}')`;
    const decoded = htmlAttrDecode(onclick);
    let received;
    const cb = (_id, n) => { received = n; };
    // eslint-disable-next-line no-new-func
    new Function('cb', decoded)(cb); // compile + execute the handler like the DOM does
    return received;
}

describe('_escJs — onclick string args survive HTML+JS decoding', () => {
    test("apostrophe name (the Rolfe's delete bug) round-trips intact", () => {
        const name = "Road trip-The Rolfe's";
        assert.equal(invokeOnclick(_escJs, name), name);
    });

    test('quotes, backslashes, ampersands, angle brackets all round-trip', () => {
        for (const name of [
            "Guns N' Roses",
            'He said "hi"',
            'back\\slash',
            'Tom & Jerry',
            '<script>alert(1)</script>',
            "O'Brien \\ \"x\" & <y>",
            "it's a \"test\" & more",
        ]) {
            assert.equal(
                invokeOnclick(_escJs, name), name,
                `round-trip failed for ${JSON.stringify(name)}`,
            );
        }
    });

    test('plain names are not over-escaped (identical to input)', () => {
        assert.equal(_escJs('Classic Rock'), 'Classic Rock');
        assert.equal(_escJs('Discover Weekly'), 'Discover Weekly');
    });

    test('empty / falsy input yields empty string', () => {
        assert.equal(_escJs(''), '');
        assert.equal(_escJs(null), '');
        assert.equal(_escJs(undefined), '');
    });
});

describe('regression: _escAttr is unsafe for the onclick JS-string context', () => {
    test('apostrophe name compiles to a SyntaxError under _escAttr (the original bug)', () => {
        const decoded = htmlAttrDecode(`cb(2, '${_escAttr("Road trip-The Rolfe's")}')`);
        // eslint-disable-next-line no-new-func
        assert.throws(() => new Function('cb', decoded), SyntaxError);
    });

    test('_escJs compiles cleanly for that same name', () => {
        const decoded = htmlAttrDecode(`cb(2, '${_escJs("Road trip-The Rolfe's")}')`);
        // eslint-disable-next-line no-new-func
        assert.doesNotThrow(() => new Function('cb', decoded));
    });
});
