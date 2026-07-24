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

describe('reduceJukebox — the pure fold every client agrees on', () => {
    const ev = (user, p) => ({ username: user, timestamp: 't', p });

    test('submissions queue, dedupe, and keep first attribution', () => {
        const st = P.reduceJukebox([
            ev('a', { k: 'jbx.sub', id: 'dQw4w9WgXcQ', ti: 'Song A' }),
            ev('b', { k: 'jbx.sub', id: 'dQw4w9WgXcQ', ti: 'Dupe' }),
            ev('b', { k: 'jbx.sub', id: 'oHg5SJYRHA0', ti: 'Song B' }),
        ]);
        assert.equal(st.queue.length, 2);
        assert.equal(st.queue[0].by, 'a');
        assert.equal(st.queue[0].ti, 'Song A');
    });

    test('votes count only for queued ids, latest per user', () => {
        const st = P.reduceJukebox([
            ev('a', { k: 'jbx.sub', id: 'aaaaaaaaaaa', ti: 'A' }),
            ev('b', { k: 'jbx.sub', id: 'bbbbbbbbbbb', ti: 'B' }),
            ev('x', { k: 'jbx.vote', o: 'aaaaaaaaaaa' }),
            ev('x', { k: 'jbx.vote', o: 'bbbbbbbbbbb' }),   // changed mind
            ev('y', { k: 'jbx.vote', o: 'zzzzzzzzzzz' }),   // not queued → ignored
        ]);
        assert.equal(st.queue.find(e => e.id === 'bbbbbbbbbbb').votes, 1);
        assert.equal(st.queue.find(e => e.id === 'aaaaaaaaaaa').votes, 0);
    });

    test('now removes the track from the queue and resets the round', () => {
        const st = P.reduceJukebox([
            ev('a', { k: 'jbx.sub', id: 'aaaaaaaaaaa', ti: 'A' }),
            ev('b', { k: 'jbx.sub', id: 'bbbbbbbbbbb', ti: 'B' }),
            ev('x', { k: 'jbx.vote', o: 'bbbbbbbbbbb' }),
            ev('dj', { k: 'jbx.now', id: 'bbbbbbbbbbb', ti: 'B', at: 1000 }),
        ]);
        assert.equal(st.now.id, 'bbbbbbbbbbb');
        assert.equal(st.now.at, 1000);
        assert.equal(st.queue.length, 1);
        assert.equal(st.tally.total, 0);                    // votes reset
    });

    test('nextTrack: winner beats FIFO, FIFO when no votes', () => {
        const base = [
            ev('a', { k: 'jbx.sub', id: 'aaaaaaaaaaa', ti: 'A' }),
            ev('b', { k: 'jbx.sub', id: 'bbbbbbbbbbb', ti: 'B' }),
        ];
        assert.equal(P.nextTrack(P.reduceJukebox(base)).id, 'aaaaaaaaaaa');
        const voted = base.concat([ev('x', { k: 'jbx.vote', o: 'bbbbbbbbbbb' })]);
        assert.equal(P.nextTrack(P.reduceJukebox(voted)).id, 'bbbbbbbbbbb');
        assert.equal(P.nextTrack(P.reduceJukebox([])), null);
    });

    test('hostile ids never enter state', () => {
        const st = P.reduceJukebox([
            ev('a', { k: 'jbx.sub', id: '<script>', ti: 'x' }),
            ev('a', { k: 'jbx.now', id: 'javascript:x', ti: 'x' }),
        ]);
        assert.equal(st.queue.length, 0);
        assert.equal(st.now, null);
    });

    test('duration rides sub and now, hostile durations dropped', () => {
        const st = P.reduceJukebox([
            ev('a', { k: 'jbx.sub', id: 'aaaaaaaaaaa', ti: 'A', d: 213 }),
            ev('b', { k: 'jbx.sub', id: 'bbbbbbbbbbb', ti: 'B', d: -5 }),
            ev('dj', { k: 'jbx.now', id: 'ccccccccccc', ti: 'C', at: 1, d: 1e9 }),
        ]);
        assert.equal(st.queue[0].d, 213);
        assert.equal(st.queue[1].d, null);
        assert.equal(st.now.d, 7200);                       // capped, not trusted
    });

    test('unsub: only the submitter can pull a track, and its votes die', () => {
        const st = P.reduceJukebox([
            ev('a', { k: 'jbx.sub', id: 'aaaaaaaaaaa', ti: 'A' }),
            ev('b', { k: 'jbx.sub', id: 'bbbbbbbbbbb', ti: 'B' }),
            ev('x', { k: 'jbx.vote', o: 'aaaaaaaaaaa' }),
            ev('mallory', { k: 'jbx.unsub', id: 'aaaaaaaaaaa' }),   // not the submitter
            ev('a', { k: 'jbx.unsub', id: 'aaaaaaaaaaa' }),         // the submitter
        ]);
        assert.equal(st.queue.length, 1);
        assert.equal(st.queue[0].id, 'bbbbbbbbbbb');
        assert.equal(st.tally.winner, null);            // the pulled track's vote vanished
    });

    test('skip votes count unique users for the CURRENT track and reset on handoff', () => {
        const base = [
            ev('dj', { k: 'jbx.now', id: 'aaaaaaaaaaa', ti: 'A', at: 1 }),
            ev('x', { k: 'jbx.skip', o: 'aaaaaaaaaaa' }),
            ev('x', { k: 'jbx.skip', o: 'aaaaaaaaaaa' }),           // same user twice = 1
            ev('y', { k: 'jbx.skip', o: 'zzzzzzzzzzz' }),           // wrong target ignored
        ];
        assert.equal(P.reduceJukebox(base).skips, 1);
        const after = base.concat([ev('dj', { k: 'jbx.now', id: 'bbbbbbbbbbb', ti: 'B', at: 2 })]);
        assert.equal(P.reduceJukebox(after).skips, 0);              // skips die with the track
    });

    test('history keeps the last plays newest-first, double-starts excluded', () => {
        const st = P.reduceJukebox([
            ev('dj', { k: 'jbx.now', id: 'aaaaaaaaaaa', ti: 'A', at: 1 }),
            ev('dj', { k: 'jbx.now', id: 'aaaaaaaaaaa', ti: 'A', at: 2 }),  // double-start
            ev('dj', { k: 'jbx.now', id: 'bbbbbbbbbbb', ti: 'B', at: 3 }),
            ev('dj', { k: 'jbx.now', id: 'ccccccccccc', ti: 'C', at: 4 }),
        ]);
        assert.equal(st.now.id, 'ccccccccccc');
        shapeEqual(st.history.map(h => h.id), ['bbbbbbbbbbb', 'aaaaaaaaaaa']);
    });

    test('queue cap holds at 25', () => {
        const evs = [];
        for (let i = 0; i < 30; i++) {
            evs.push(ev('u' + i, { k: 'jbx.sub', id: 'id' + String(i).padStart(9, '0'), ti: 'T' + i }));
        }
        assert.equal(P.reduceJukebox(evs).queue.length, 25);
    });
});

describe('reducePins — the shared pin board', () => {
    const ev = (user, p) => ({ username: user, timestamp: 't', p });
    const add = (by, u, ts, x) => ev(by, { k: 'pin.add', u, ts, x });

    test('add, dedupe (re-pin moves to newest), delete', () => {
        const pins = P.reducePins([
            add('a', 'bob', 't1', 'first'),
            add('a', 'sue', 't2', 'second'),
            add('b', 'bob', 't1', 'again'),          // re-pin same message
            ev('c', { k: 'pin.del', u: 'sue', ts: 't2' }),
        ]);
        assert.equal(pins.length, 1);
        assert.equal(pins[0].u, 'bob');
        assert.equal(pins[0].by, 'b');               // latest pinner wins
    });

    test('board caps at 8 — oldest falls off', () => {
        const evs = [];
        for (let i = 0; i < 10; i++) evs.push(add('a', 'u' + i, 't' + i, 'x'));
        const pins = P.reducePins(evs);
        assert.equal(pins.length, 8);
        assert.equal(pins[0].u, 'u2');
    });

    test('garbage never lands', () => {
        assert.equal(P.reducePins([ev('a', { k: 'pin.add', u: '', ts: 't' })]).length, 0);
        assert.equal(P.reducePins([ev('a', { k: 'pin.add', u: 'x'.repeat(99), ts: 't' })]).length, 0);
    });
});

describe('reducePoll — one active poll, latest start wins', () => {
    const ev = (user, p, ts) => ({ username: user, timestamp: ts || 't', p });

    test('start + votes + tally, latest vote per user', () => {
        const poll = P.reducePoll([
            ev('op', { k: 'poll.start', q: 'best daft punk album?', o1: 'Discovery', o2: 'RAM' }),
            ev('x', { k: 'poll.vote', o: '1' }),
            ev('y', { k: 'poll.vote', o: '2' }),
            ev('x', { k: 'poll.vote', o: '2' }),
        ]);
        assert.equal(poll.q, 'best daft punk album?');
        shapeEqual(poll.tally.counts, { '2': 2 });
        assert.equal(poll.closed, false);
    });

    test('a new start resets everything', () => {
        const poll = P.reducePoll([
            ev('op', { k: 'poll.start', q: 'old?', o1: 'a', o2: 'b' }),
            ev('x', { k: 'poll.vote', o: '1' }),
            ev('op2', { k: 'poll.start', q: 'new?', o1: 'c', o2: 'd' }),
        ]);
        assert.equal(poll.q, 'new?');
        assert.equal(poll.tally.total, 0);
    });

    test('only the starter can end it; votes stop after close', () => {
        const poll = P.reducePoll([
            ev('op', { k: 'poll.start', q: 'q?', o1: 'a', o2: 'b' }),
            ev('mallory', { k: 'poll.end' }),
            ev('x', { k: 'poll.vote', o: '1' }),
            ev('op', { k: 'poll.end' }),
            ev('y', { k: 'poll.vote', o: '2' }),      // too late
        ]);
        assert.equal(poll.closed, true);
        shapeEqual(poll.tally.counts, { '1': 1 });
    });

    test('out-of-range and hostile votes are ignored', () => {
        const poll = P.reducePoll([
            ev('op', { k: 'poll.start', q: 'q?', o1: 'a', o2: 'b' }),
            ev('x', { k: 'poll.vote', o: '3' }),      // only 2 options exist
            ev('y', { k: 'poll.vote', o: 'zz' }),
        ]);
        assert.equal(poll.tally.total, 0);
    });

    test('degenerate starts are refused', () => {
        assert.equal(P.reducePoll([ev('op', { k: 'poll.start', q: 'q?', o1: 'only' })]), null);
        assert.equal(P.reducePoll([ev('op', { k: 'poll.start', q: '', o1: 'a', o2: 'b' })]), null);
        assert.equal(P.reducePoll([]), null);
    });
});

describe('reduceTopic + reduceTuned', () => {
    const ev = (user, p) => ({ username: user, timestamp: 't', p });

    test('latest topic wins, empty clears', () => {
        shapeEqual(P.reduceTopic([
            ev('a', { k: 'topic.set', t: 'share your vinyl finds' }),
            ev('b', { k: 'topic.set', t: 'friday listening party' }),
        ]), { t: 'friday listening party', by: 'b' });
        assert.equal(P.reduceTopic([
            ev('a', { k: 'topic.set', t: 'x' }),
            ev('b', { k: 'topic.set', t: '' }),
        ]), null);
    });

    test('tuned presence follows the latest on/off per user', () => {
        shapeEqual(P.reduceTuned([
            ev('a', { k: 'jbx.tune', on: 1 }),
            ev('b', { k: 'jbx.tune', on: 1 }),
            ev('a', { k: 'jbx.tune', on: 0 }),
        ]), { b: 1 });
    });
});
