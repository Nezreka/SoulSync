// SoulSync chat protocol — the pure logic under the room's hidden message bus.
//
// The Soulseek room is plain text, but every SoulSync message carries the
// !SS1! envelope (see core/chat_codec.py). Envelopes can carry a protocol
// object under "p" ({k: "<kind>", ...}) invisible to vanilla clients. This
// file is the DETERMINISTIC core those features share: user classification,
// protocol parsing (hostile input!), vote tallying, and coordinator election.
// Every client sees the same message stream, so pure functions over that
// stream agree everywhere without any server or coordination chatter.
//
// Zero DOM, zero fetch — loaded before chat.js, unit-tested in isolation
// (tests/static/test_chat_protocol.mjs).

(function () {
    'use strict';

    // ── User classification ─────────────────────────────────────────────
    // The FLIP (Boulder): assume everyone is a SoulSync user until they
    // demonstrate otherwise. An envelope message proves SoulSync — forever
    // (clients always wrap, so one envelope is conclusive). A bare-text
    // message from a user who has NEVER sent an envelope marks them vanilla;
    // a later envelope promotes them (client upgrade mid-session).
    //   states: 'assumed' (never spoke) → 'soulsync' | 'vanilla'
    function classifyUser(current, messageIsRich) {
        if (messageIsRich) return 'soulsync';
        return current === 'soulsync' ? 'soulsync' : 'vanilla';
    }

    // ── Protocol payload parsing (REMOTE data — trust nothing) ──────────
    // {k: kind, ...} where kind is a short dotted identifier ('jbx.vote').
    // Caps: kind ≤ 24 chars, ≤ 16 fields, strings ≤ 512 chars, numbers
    // finite, one level of nesting for plain-object/array values with the
    // same caps. Anything else → null (callers drop it silently).
    var KIND_RE = /^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)?$/;

    function _saneScalar(v) {
        if (typeof v === 'string') return v.length <= 512;
        if (typeof v === 'number') return isFinite(v);
        return typeof v === 'boolean' || v === null;
    }

    function _sanePayload(obj, depth) {
        if (obj === null || typeof obj !== 'object') return false;
        var keys = Object.keys(obj);
        if (keys.length > 16) return false;
        for (var i = 0; i < keys.length; i++) {
            var v = obj[keys[i]];
            if (_saneScalar(v)) continue;
            if (depth > 0 && Array.isArray(v)) {
                if (v.length > 32 || !v.every(function (x) { return _saneScalar(x); })) return false;
            } else if (depth > 0 && typeof v === 'object') {
                if (!_sanePayload(v, depth - 1)) return false;
            } else {
                return false;
            }
        }
        return true;
    }

    function parseProtocol(envelope) {
        try {
            if (!envelope || typeof envelope !== 'object') return null;
            var p = envelope.p;
            if (!p || typeof p !== 'object' || Array.isArray(p)) return null;
            if (typeof p.k !== 'string' || p.k.length > 24 || !KIND_RE.test(p.k)) return null;
            if (!_sanePayload(p, 1)) return null;
            return p;
        } catch (e) {
            return null;
        }
    }

    // ── Deterministic vote tally ────────────────────────────────────────
    // votes: [{username, option, timestamp}] in stream order. Rules every
    // client applies identically: a user's LATEST vote wins (stream order —
    // same stream everywhere); winner = most votes, ties broken by
    // lexicographically-smallest option id (stable, no randomness).
    function tallyVotes(votes) {
        var byUser = {};
        (votes || []).forEach(function (v) {
            if (!v || typeof v.username !== 'string' || typeof v.option !== 'string') return;
            if (!v.username || !v.option || v.option.length > 128) return;
            byUser[v.username] = v.option;
        });
        var counts = {};
        Object.keys(byUser).forEach(function (u) {
            counts[byUser[u]] = (counts[byUser[u]] || 0) + 1;
        });
        var winner = null, best = -1;
        Object.keys(counts).sort().forEach(function (opt) {   // lexicographic walk = stable ties
            if (counts[opt] > best) { best = counts[opt]; winner = opt; }
        });
        return { counts: counts, winner: winner, total: Object.keys(byUser).length };
    }

    // ── Coordinator ("DJ") election ─────────────────────────────────────
    // Deterministic and chatter-free: everyone computes the same coordinator
    // from the same participant set — lexicographically-smallest active
    // SoulSync username (case-insensitive, then case-sensitive tiebreak).
    function electCoordinator(usernames) {
        var pool = (usernames || []).filter(function (u) {
            return typeof u === 'string' && u.length > 0;
        });
        if (!pool.length) return null;
        pool.sort(function (a, b) {
            var al = a.toLowerCase(), bl = b.toLowerCase();
            if (al !== bl) return al < bl ? -1 : 1;
            return a < b ? -1 : (a > b ? 1 : 0);
        });
        return pool[0];
    }

    window.ChatProtocol = {
        classifyUser: classifyUser,
        parseProtocol: parseProtocol,
        tallyVotes: tallyVotes,
        electCoordinator: electCoordinator,
    };
})();
