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

    // ── Jukebox reducer ─────────────────────────────────────────────────
    // The whole room jukebox is a PURE FOLD over the protocol event stream:
    // every client reduces the same events to the same state — queue, votes,
    // now-playing — with zero coordination chatter. Rules:
    //   jbx.sub  {id, ti}      → append to queue (dedupe by id, cap 25,
    //                            first submitter keeps attribution)
    //   jbx.vote {o: id}       → latest vote per user among CURRENTLY queued
    //   jbx.now  {id, ti, at}  → becomes now-playing; its id leaves the queue
    //                            and ALL votes reset (new round)
    // Event order is the slskd stream order — identical on every client.
    var _YT_ID_RE = /^[A-Za-z0-9_-]{6,16}$/;

    function _saneDuration(v) {
        return (typeof v === 'number' && isFinite(v) && v > 0)
            ? Math.min(Math.floor(v), 7200) : null;
    }

    function reduceJukebox(events) {
        var queue = [];          // [{id, ti, by}]
        var inQueue = {};        // id → queue entry
        var votes = [];          // raw votes since last round
        var skipVotes = [];      // votes to skip the CURRENT track
        var history = [];        // previously played, newest first (cap 10)
        var radio = false;       // auto-DJ: DJ tops up an empty queue (shared toggle)
        var now = null;

        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            var p = ev.p;
            if (p.k === 'jbx.sub') {
                var id = String(p.id || '');
                if (!_YT_ID_RE.test(id) || inQueue[id]) return;
                if (now && now.id === id) return;          // already playing
                if (queue.length >= 25) return;            // cap: no queue bombs
                var entry = { id: id, ti: String(p.ti || '').slice(0, 120),
                              d: _saneDuration(p.d), by: ev.username };
                if (p.a) entry.auto = true;        // queued by the auto-DJ
                // why the auto-DJ chose it ("similar to X") — display credit only
                if (p.w) entry.why = String(p.w).slice(0, 60);
                queue.push(entry);
                inQueue[id] = entry;
            } else if (p.k === 'jbx.vote') {
                var o = String(p.o || '');
                if (inQueue[o]) votes.push({ username: ev.username, option: o });
            } else if (p.k === 'jbx.unsub') {
                // only the submitter can pull their own track
                var uid = String(p.id || '');
                var ent = inQueue[uid];
                if (ent && ent.by === ev.username) {
                    queue = queue.filter(function (e) { return e.id !== uid; });
                    delete inQueue[uid];
                }
            } else if (p.k === 'jbx.radio') {
                radio = !!p.on;                    // latest toggle wins, any sender
            } else if (p.k === 'jbx.skip') {
                var so = String(p.o || '');
                if (now && so === now.id) skipVotes.push({ username: ev.username, option: so });
            } else if (p.k === 'jbx.now') {
                var nid = String(p.id || '');
                if (!_YT_ID_RE.test(nid)) return;
                if (now && now.id !== nid) {          // a real handoff, not a double-start
                    history.unshift(now);
                    if (history.length > 10) history.pop();
                }
                now = { id: nid, ti: String(p.ti || '').slice(0, 120),
                        at: (typeof p.at === 'number' && isFinite(p.at)) ? p.at : null,
                        d: _saneDuration(p.d), by: ev.username };
                if (inQueue[nid]) {
                    queue = queue.filter(function (e) { return e.id !== nid; });
                    delete inQueue[nid];
                }
                votes = [];                                // new round
                skipVotes = [];                            // skips die with the track
            }
        });

        votes = votes.filter(function (v) { return inQueue[v.option]; });   // unsub'd ids drop out
        var tally = tallyVotes(votes);
        queue.forEach(function (e) { e.votes = tally.counts[e.id] || 0; });
        return { queue: queue, now: now, tally: tally,
                 skips: tallyVotes(skipVotes).total, history: history, radio: radio };
    }

    // ── Pins / poll / topic / tuned-presence reducers ───────────────────
    // Same discipline as the jukebox: each is a pure fold over the room's
    // protocol events, so every SoulSync client shows the same board.

    // pin.add {u, ts, x} / pin.del {u, ts} → rolling board of pinned
    // messages (dedupe by author|timestamp, cap 8 — oldest falls off).
    function reducePins(events) {
        var pins = [];
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            var p = ev.p;
            if (p.k !== 'pin.add' && p.k !== 'pin.del') return;
            var u = String(p.u || ''), ts = String(p.ts || '');
            if (!u || !ts || u.length > 64 || ts.length > 64) return;
            var key = u + '|' + ts;
            pins = pins.filter(function (e) { return e.key !== key; });
            if (p.k === 'pin.add') {
                pins.push({ key: key, u: u, ts: ts,
                            x: String(p.x || '').slice(0, 140), by: ev.username });
                if (pins.length > 8) pins.shift();
            }
        });
        return pins;
    }

    // poll.start {q, o1..o4} / poll.vote {o: '1'..'4'} / poll.end {} →
    // ONE active poll per room, latest start wins and resets votes; only
    // the starter's poll.end closes it. Votes ride tallyVotes (latest per
    // user), restricted to the poll's real option indices.
    function reducePoll(events) {
        var poll = null;
        var votes = [];
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            var p = ev.p;
            if (p.k === 'poll.start') {
                var opts = [];
                for (var i = 1; i <= 4; i++) {
                    var o = p['o' + i];
                    if (typeof o === 'string' && o.trim()) opts.push(o.trim().slice(0, 80));
                }
                var qq = String(p.q || '').trim().slice(0, 160);
                if (!qq || opts.length < 2) return;
                poll = { q: qq, options: opts, by: ev.username,
                         at: ev.timestamp, closed: false };
                votes = [];
            } else if (p.k === 'poll.vote' && poll && !poll.closed) {
                var idx = String(p.o || '');
                if (/^[1-4]$/.test(idx) && parseInt(idx, 10) <= poll.options.length) {
                    votes.push({ username: ev.username, option: idx });
                }
            } else if (p.k === 'poll.end' && poll && ev.username === poll.by) {
                poll.closed = true;
            }
        });
        if (!poll) return null;
        poll.tally = tallyVotes(votes);
        return poll;
    }

    // topic.set {t} → latest wins; empty clears.
    function reduceTopic(events) {
        var topic = null;
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            if (ev.p.k !== 'topic.set') return;
            var t = String(ev.p.t || '').trim().slice(0, 160);
            topic = t ? { t: t, by: ev.username } : null;
        });
        return topic;
    }

    // jbx.tune {on: 1|0} → who is listening right now (latest per user).
    function reduceTuned(events) {
        var tuned = {};
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            if (ev.p.k !== 'jbx.tune') return;
            if (ev.p.on) tuned[ev.username] = 1;
            else delete tuned[ev.username];
        });
        return tuned;
    }

    // np.set {t, a} → what each user is playing in SoulSync's OWN player
    // (latest per user; an empty title means they stopped). Opt-in on the
    // sender's side — this is a public room, so nothing is broadcast unless
    // the user turned it on.
    function reduceNowPlaying(events) {
        var np = {};
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            if (ev.p.k !== 'np.set') return;
            var t = String(ev.p.t || '').slice(0, 120);
            if (!t) { delete np[ev.username]; return; }
            np[ev.username] = { t: t, a: String(ev.p.a || '').slice(0, 80) };
        });
        return np;
    }

    // Preset avatar ids announced by the 'hello' beacon ({k:'hello', av:N}), so
    // someone who joined but hasn't spoken still has a face. Messages carry the
    // id too (see _avatarOf in chat.js) — this covers the silent ones. Bounded
    // to the known set: the id INDEXES a fixed list, it never builds a path.
    function reduceAvatars(events, maxId) {
        var out = {};
        var cap = (typeof maxId === 'number' && maxId > 0) ? maxId : 99;
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            var n = parseInt(ev.p.av, 10);
            if (n >= 1 && n <= cap) out[ev.username] = n;
        });
        return out;
    }

    // The next track every client agrees on: most votes (lexicographic tie),
    // FIFO head when nobody voted. Null when the queue is empty.
    function nextTrack(state) {
        if (!state || !state.queue || !state.queue.length) return null;
        if (state.tally && state.tally.winner) {
            for (var i = 0; i < state.queue.length; i++) {
                if (state.queue[i].id === state.tally.winner) return state.queue[i];
            }
        }
        return state.queue[0];
    }

    window.ChatProtocol = {
        classifyUser: classifyUser,
        parseProtocol: parseProtocol,
        tallyVotes: tallyVotes,
        electCoordinator: electCoordinator,
        reduceNowPlaying: reduceNowPlaying,
        reduceAvatars: reduceAvatars,
        reduceJukebox: reduceJukebox,
        nextTrack: nextTrack,
        reducePins: reducePins,
        reducePoll: reducePoll,
        reduceTopic: reduceTopic,
        reduceTuned: reduceTuned,
    };
})();
