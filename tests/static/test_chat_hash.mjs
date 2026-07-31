// SHA-256 contract (webui/static/chat-hash.js).
// Run via: node --test tests/static/test_chat_hash.mjs
//
// A commitment is only worth something if it is genuinely hard to find a
// second input matching it. A subtly wrong SHA-256 would still produce
// plausible-looking hex and still "work" in the game, while quietly being
// forgeable — so this checks against the published vectors AND against
// node's own crypto, rather than against itself.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { createHash, randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const ctx = { window: {}, Math };
vm.createContext(ctx);
vm.runInContext(readFileSync(resolve(here, '../../webui/static/chat-hash.js'), 'utf-8'), ctx);
const H = ctx.window.ChatHash;

const ref = s => createHash('sha256').update(s, 'utf8').digest('hex');

describe('sha256 — the published vectors', () => {
    test('empty string', () => {
        assert.equal(H.sha256(''),
            'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    });
    test('abc', () => {
        assert.equal(H.sha256('abc'),
            'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    });
    test('the 448-bit block boundary case', () => {
        assert.equal(H.sha256('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq'),
            '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1');
    });
    test('a million a', () => {
        assert.equal(H.sha256('a'.repeat(1000000)),
            'cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0');
    });
});

describe('sha256 — agrees with node for anything thrown at it', () => {
    test('lengths around every padding boundary', () => {
        // 55/56/63/64/119/120 are where the length block and an extra chunk
        // kick in; an off-by-one in padding shows up here and nowhere else.
        for (const n of [0, 1, 54, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 200]) {
            const s = 'x'.repeat(n);
            assert.equal(H.sha256(s), ref(s), `length ${n}`);
        }
    });
    test('utf-8: accents, cjk and emoji', () => {
        // charCodeAt-based hashing would diverge from every other tool here,
        // and two clients with different names would disagree.
        for (const s of ['café', 'naïve', '日本語', 'кириллица', '🎲♟️', 'a🎲b',
                         'BoulderBadgeDad', 'sella|1|2|3']) {
            assert.equal(H.sha256(s), ref(s), JSON.stringify(s));
        }
    });
    test('random inputs', () => {
        for (let i = 0; i < 200; i++) {
            const s = randomBytes(1 + (i % 90)).toString('base64');
            assert.equal(H.sha256(s), ref(s), s);
        }
    });
    test('null and undefined hash as the empty string, never throw', () => {
        assert.equal(H.sha256(null), ref(''));
        assert.equal(H.sha256(undefined), ref(''));
    });
});

describe('commitments', () => {
    test('a commitment verifies against its own inputs', () => {
        const s = H.salt();
        const c = H.commit(s, 'A1,B2,C3');
        assert.ok(H.verify(c, s, 'A1,B2,C3'));
    });
    test('changing the payload breaks it — the whole point', () => {
        const s = H.salt();
        const c = H.commit(s, 'A1,B2,C3');
        assert.equal(H.verify(c, s, 'A1,B2,C4'), false, 'a different board');
        assert.equal(H.verify(c, 'other-salt', 'A1,B2,C3'), false, 'a different salt');
    });
    test('the separator stops two different inputs colliding', () => {
        // Without a delimiter, ("ab","c") and ("a","bc") would commit alike.
        assert.notEqual(H.commit('ab', 'c'), H.commit('a', 'bc'));
    });
    test('garbage never verifies', () => {
        const s = H.salt();
        const c = H.commit(s, 'board');
        for (const bad of ['', null, undefined, 42, {}, [], c.slice(0, 8), c + 'ff']) {
            assert.equal(H.verify(bad, s, 'board'), false, JSON.stringify(bad));
        }
    });
    test('128 bits is kept — envelope space is scarce, not the security', () => {
        assert.equal(H.commit('a', 'b').length, 32);
    });
    test('salts differ', () => {
        const seen = new Set();
        for (let i = 0; i < 50; i++) seen.add(H.salt());
        assert.equal(seen.size, 50);
    });
});
