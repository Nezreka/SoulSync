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
    // Caps: kind ≤ 24 chars, ≤ 16 fields, strings ≤ 512 chars, numbers finite
    // and under 1e15, one level of nesting for plain-object/array values with
    // the same caps. Anything else → null (callers drop it silently).
    // These MUST match core/chat_codec.protocol_of — see _saneScalar.
    var KIND_RE = /^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)?$/;

    // 1e15 is not decoration: core/chat_codec.protocol_of caps magnitude there,
    // and it is the gatekeeper at BOTH ends — it validates what the server hands
    // us AND what we are allowed to send. isFinite() alone let this side accept
    // numbers python would refuse, so a client could build a payload its own
    // validator blessed and get a 400 back, while an inbound carrier that python
    // dropped simply never reached the bus. Same number, both files, or clients
    // desync exactly as the codec docstring warns.
    var MAX_ABS = 1e15;

    function _saneScalar(v) {
        if (typeof v === 'string') return v.length <= 512;
        if (typeof v === 'number') return isFinite(v) && Math.abs(v) < MAX_ABS;
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
    // ── Moderators ────────────────────────────────────────────────────
    // The RESERVED_AVATARS trust model extended to ACTIONS: the slskd
    // sender name on a room message cannot be forged, so every client can
    // verify a moderator event locally, and non-moderator copies of the
    // same event are ignored by everyone. Mirrored nowhere — this list is
    // the single source both reducers and chat.js UI gating read.
    var CHAT_MODERATORS = ['boulderbadgedad'];

    function isModerator(username) {
        return CHAT_MODERATORS.indexOf(String(username || '').trim().toLowerCase()) !== -1;
    }

    // mod.hide {u, ts} / mod.unhide {u, ts} → { 'u|ts': true } of messages
    // every SoulSync client renders collapsed. Moderator-only by fold rule.
    function reduceHidden(events) {
        var hidden = {};
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            var p = ev.p;
            if (p.k !== 'mod.hide' && p.k !== 'mod.unhide') return;
            if (!isModerator(ev.username)) return;
            var u = String(p.u || ''), ts = String(p.ts || '');
            if (!u || !ts || u.length > 64 || ts.length > 64) return;
            if (p.k === 'mod.hide') hidden[u + '|' + ts] = true;
            else delete hidden[u + '|' + ts];
        });
        return hidden;
    }

    // mod.gamekill {id} → { id: true }. A killed game vanishes from the
    // arcade fold on every client. Moderator-only by fold rule.
    function reduceGameKills(events) {
        var kills = {};
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            if (ev.p.k !== 'mod.gamekill') return;
            if (!isModerator(ev.username)) return;
            var id = String(ev.p.id || '');
            if (id && id.length <= 64) kills[id] = true;
        });
        return kills;
    }

    function reducePins(events) {
        var pins = [];
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            var p = ev.p;
            if (p.k !== 'pin.add' && p.k !== 'pin.del') return;
            // Owner-only pins (Boulder): the board folds moderator events
            // exclusively, so anyone else's pin.add is inert on every client.
            if (!isModerator(ev.username)) return;
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

    // ── Watch-together reducer ──────────────────────────────────────────
    // Movie night as a pure fold, same discipline as the jukebox. ONE party
    // per room. Nominations form a ballot; votes ride tallyVotes; a start
    // consumes a live nomination and anchors the party clock to the STREAM
    // timestamp (the shared clock every client already agrees on — no client
    // wall time on the wire). Playback position is derived state:
    //   pos = offsetMs + (streamNow - basisAt)   while playing
    //   pos = offsetMs                            while paused
    // Carriers:
    //   watch.nom  {id, kd, ti, y, s, e, po} → ballot entry (dedupe by
    //              kd:id[:SxE], cap 12, first nominator keeps attribution)
    //   watch.unnom {o}   → nominator (or a moderator) pulls a nomination
    //   watch.vote {o}    → latest vote per user among LIVE nominations
    //   watch.start {o}   → a live nomination becomes the showing; ballot
    //              entry leaves, votes reset; latest start wins (a new
    //              start replaces the current showing → history)
    //   watch.pause / watch.resume {} → starter or moderator only
    //   watch.end {}      → starter or moderator; showing → history
    var _TMDB_ID_RE = /^[0-9]{1,10}$/;

    // slskd stream timestamps arrive as ISO strings OR ms numbers depending
    // on the transport hop (chat-games learned this the hard way — its _ts()
    // twin). Null = unclocked; clock-bearing carriers ignore such events.
    function _streamTs(ev) {
        var t = ev && ev.timestamp;
        if (typeof t === 'number' && isFinite(t)) return t;
        var ms = Date.parse(t);
        return isFinite(ms) ? ms : null;
    }

    function _watchKey(p) {
        var id = String(p.id || '');
        if (!_TMDB_ID_RE.test(id)) return null;
        var kd = p.kd === 't' ? 't' : (p.kd === 'm' ? 'm' : null);
        if (!kd) return null;
        if (kd === 't') {
            var s = parseInt(p.s, 10), e = parseInt(p.e, 10);
            if (!(s >= 0 && s <= 999 && e >= 0 && e <= 9999)) return null;
            return 't:' + id + ':' + s + 'x' + e;
        }
        return 'm:' + id;
    }

    function reduceWatch(events) {
        var noms = [];        // [{key, id, kd, s, e, ti, y, po, by}]
        var byKey = {};
        var votes = [];
        var now = null;       // {key, id, kd, s, e, ti, y, po, by, at, paused, basisAt, offsetMs}
        var history = [];     // finished showings, newest first (cap 5)

        function _retire(showing) {
            history.unshift({ key: showing.key, ti: showing.ti, by: showing.by });
            if (history.length > 5) history.pop();
        }

        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            var p = ev.p;
            var ts = _streamTs(ev);
            if (p.k === 'watch.nom') {
                var key = _watchKey(p);
                if (!key || byKey[key]) return;
                if (now && now.key === key) return;        // already showing
                if (noms.length >= 12) return;             // no ballot bombs
                var entry = { key: key, id: String(p.id), kd: p.kd,
                              ti: String(p.ti || '').slice(0, 120),
                              y: String(p.y || '').slice(0, 4),
                              po: String(p.po || '').slice(0, 200),
                              by: ev.username };
                if (p.kd === 't') { entry.s = parseInt(p.s, 10); entry.e = parseInt(p.e, 10); }
                noms.push(entry);
                byKey[key] = entry;
            } else if (p.k === 'watch.unnom') {
                var uo = String(p.o || '');
                var ent = byKey[uo];
                if (ent && (ent.by === ev.username || isModerator(ev.username))) {
                    noms = noms.filter(function (n) { return n.key !== uo; });
                    delete byKey[uo];
                }
            } else if (p.k === 'watch.vote') {
                var o = String(p.o || '');
                if (byKey[o]) votes.push({ username: ev.username, option: o });
            } else if (p.k === 'watch.start') {
                var so = String(p.o || '');
                var pick = byKey[so];
                if (!pick || ts === null) return;          // unclocked events can't anchor
                if (now) _retire(now);                     // latest start wins
                now = { key: pick.key, id: pick.id, kd: pick.kd, s: pick.s, e: pick.e,
                        ti: pick.ti, y: pick.y, po: pick.po, by: ev.username,
                        at: ts, paused: false, basisAt: ts, offsetMs: 0 };
                noms = noms.filter(function (n) { return n.key !== so; });
                delete byKey[so];
                votes = [];                                // new ballot round
            } else if (p.k === 'watch.pause') {
                if (!now || now.paused || ts === null) return;
                if (ev.username !== now.by && !isModerator(ev.username)) return;
                now.offsetMs += Math.max(0, ts - now.basisAt);
                now.paused = true;
            } else if (p.k === 'watch.resume') {
                if (!now || !now.paused || ts === null) return;
                if (ev.username !== now.by && !isModerator(ev.username)) return;
                now.basisAt = ts;
                now.paused = false;
            } else if (p.k === 'watch.end') {
                if (!now) return;
                if (ev.username !== now.by && !isModerator(ev.username)) return;
                _retire(now);
                now = null;
            }
        });

        votes = votes.filter(function (v) { return byKey[v.option]; });
        var tally = tallyVotes(votes);
        noms.forEach(function (n) { n.votes = tally.counts[n.key] || 0; });
        return { noms: noms, now: now, tally: tally, history: history };
    }

    // The party's playback position in ms at stream-time nowMs — derived,
    // never stored, so every client that agrees on the fold agrees on it.
    function watchPosition(now, nowMs) {
        if (!now) return null;
        if (now.paused) return now.offsetMs;
        return now.offsetMs + Math.max(0, nowMs - now.basisAt);
    }

    // ── Trivia reducer ──────────────────────────────────────────────────
    // The stream is an unforgeable buzzer: everyone sees the same guesses in
    // the same order, so "first correct answer" needs no judge. The asker
    // posts trv.ask with the sha256 of the NORMALIZED answer — every client
    // checks every guess against it locally (the hash is deliberately
    // unsalted: checkability by all is the point; a determined player could
    // hash-test candidates offline, which is just… playing, quietly).
    //   trv.ask   {id, q, h, pot} → one live question per room, latest wins
    //   trv.guess {id, a}         → first hash match in stream order takes it;
    //                               the asker's own guesses never count
    //   trv.end   {id, ans}       → asker (or a moderator) closes unanswered;
    //                               ans is displayed, verified against h
    // The hash function is INJECTED (this file stays dependency-free);
    // chat.js passes ChatHash.sha256. Both sides of the wire must hash the
    // same normalization — normalizeTriviaAnswer is that single definition.
    function normalizeTriviaAnswer(s) {
        return String(s || '')
            .toLowerCase()
            .replace(/[''"".,!?;:()\[\]\-–—_/\\]/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
            .replace(/^(the|a|an) /, '');
    }

    var TRIV_ID_RE = /^[a-z0-9]{4,16}$/;

    function reduceTrivia(events, hashFn) {
        var t = null;
        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string') return;
            var p = ev.p;
            if (p.k === 'trv.ask') {
                var id = String(p.id || '');
                if (!TRIV_ID_RE.test(id)) return;
                var q = String(p.q || '').trim().slice(0, 200);
                var h = String(p.h || '');
                if (!q || !/^[0-9a-f]{16,64}$/.test(h)) return;
                var pot = 0;
                if (typeof p.pot === 'number' && isFinite(p.pot)) {
                    pot = Math.max(0, Math.min(500, Math.floor(p.pot)));
                }
                t = { id: id, q: q, h: h, pot: pot, by: ev.username,
                      at: _streamTs(ev), guesses: [], winner: '', winAnswer: '',
                      closed: false, answer: '', verified: false };
            } else if (p.k === 'trv.guess' && t && !t.closed) {
                if (String(p.id || '') !== t.id) return;
                if (ev.username === t.by) return;      // no winning your own pot
                var a = String(p.a || '').slice(0, 120);
                if (!a.trim()) return;
                var ok = typeof hashFn === 'function' &&
                         hashFn(normalizeTriviaAnswer(a)) === t.h;
                t.guesses.push({ u: ev.username, a: a, ok: ok });
                if (t.guesses.length > 200) t.guesses.shift();
                if (ok) { t.winner = ev.username; t.winAnswer = a; t.closed = true; }
            } else if (p.k === 'trv.end' && t && !t.closed) {
                if (String(p.id || '') !== t.id) return;
                if (ev.username !== t.by && !isModerator(ev.username)) return;
                t.closed = true;
                t.answer = String(p.ans || '').slice(0, 120);
                t.verified = typeof hashFn === 'function' &&
                             hashFn(normalizeTriviaAnswer(t.answer)) === t.h;
            }
        });
        return t;
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
        isModerator: isModerator,
        reduceHidden: reduceHidden,
        reduceGameKills: reduceGameKills,
        reducePins: reducePins,
        reducePoll: reducePoll,
        reduceWatch: reduceWatch,
        watchPosition: watchPosition,
        reduceTrivia: reduceTrivia,
        normalizeTriviaAnswer: normalizeTriviaAnswer,
        reduceTopic: reduceTopic,
        reduceTuned: reduceTuned,
    };
})();
