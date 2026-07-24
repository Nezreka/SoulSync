// Deterministic core of the SoulSync chat protocol (chat-protocol.js).
// Run via: node --test tests/static/test_chat_protocol.mjs
// (pytest wrapper: tests/test_chat_protocol_js.py)
//
// These functions must agree across every client observing the same message
// stream — determinism IS the feature, so the tests hammer ordering, ties,
// and hostile input.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, '../../webui/static/chat-protocol.js'), 'utf-8');
const ctx = { window: {} };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const P = ctx.window.ChatProtocol;

// VM-created objects are cross-realm (different prototype chain) — compare
// shapes via JSON round-trip, same pattern as test_auto_sync.mjs.
function shapeEqual(actual, expected, msg) {
    assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected, msg);
}

describe('classifyUser — the assume-SoulSync flip', () => {
    test('unknown user stays capable until proven vanilla', () => {
        assert.equal(P.classifyUser(undefined, false), 'vanilla');
        assert.equal(P.classifyUser('assumed', false), 'vanilla');
    });
    test('an envelope is conclusive, forever', () => {
        assert.equal(P.classifyUser('assumed', true), 'soulsync');
        assert.equal(P.classifyUser('vanilla', true), 'soulsync');   // mid-session upgrade
        assert.equal(P.classifyUser('soulsync', false), 'soulsync'); // plain text never demotes
    });
});

describe('parseProtocol — hostile input', () => {
    test('valid kinds parse', () => {
        shapeEqual(P.parseProtocol({ p: { k: 'jbx.vote', o: 'abc' } }),
                   { k: 'jbx.vote', o: 'abc' });
        assert.equal(P.parseProtocol({ p: { k: 'np', t: 'song' } }).k, 'np');
    });
    test('garbage is rejected, never throws', () => {
        for (const bad of [null, {}, { p: null }, { p: [] }, { p: { k: 42 } },
                           { p: { k: 'UPPER' } }, { p: { k: 'a'.repeat(30) } },
                           { p: { k: 'x.y', blob: { deep: { deeper: 1 } } } },
                           { p: { k: 'x', s: 'y'.repeat(600) } },
                           { p: { k: 'x', fn: () => 1 } }]) {
            assert.equal(P.parseProtocol(bad), null, JSON.stringify(String(bad)));
        }
    });
    test('field-count bomb rejected', () => {
        const p = { k: 'x' };
        for (let i = 0; i < 20; i++) p['f' + i] = i;
        assert.equal(P.parseProtocol({ p }), null);
    });
});

describe('tallyVotes — determinism', () => {
    test('latest vote per user wins', () => {
        const r = P.tallyVotes([
            { username: 'a', option: 'song1' },
            { username: 'b', option: 'song2' },
            { username: 'a', option: 'song2' },   // a changed their mind
        ]);
        shapeEqual(r.counts, { song2: 2 });
        assert.equal(r.winner, 'song2');
        assert.equal(r.total, 2);
    });
    test('ties break lexicographically — stable everywhere', () => {
        const r = P.tallyVotes([
            { username: 'a', option: 'zed' },
            { username: 'b', option: 'alpha' },
        ]);
        assert.equal(r.winner, 'alpha');
    });
    test('malformed votes are ignored', () => {
        const r = P.tallyVotes([
            { username: 'a', option: 'ok' },
            { username: '', option: 'x' },
            { username: 'b' },
            null,
            { username: 'c', option: 'y'.repeat(200) },
        ]);
        shapeEqual(r.counts, { ok: 1 });
    });
    test('empty stream → no winner', () => {
        assert.equal(P.tallyVotes([]).winner, null);
    });
});

describe('electCoordinator — chatter-free agreement', () => {
    test('lexicographically-smallest wins, case-insensitive', () => {
        assert.equal(P.electCoordinator(['zeta', 'Alpha', 'beta']), 'Alpha');
    });
    test('case tiebreak is deterministic', () => {
        assert.equal(P.electCoordinator(['Bob', 'bob']), 'Bob');
        assert.equal(P.electCoordinator(['bob', 'Bob']), 'Bob');   // order-independent
    });
    test('empty pool → null', () => {
        assert.equal(P.electCoordinator([]), null);
        assert.equal(P.electCoordinator(null), null);
    });
});
