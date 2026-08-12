// SoulSync Arcade — the game lifecycle fold.
//
// There is no game server. A match is nothing but protocol carriers in the
// Soulseek room, and every client folds the same carriers into the same
// game. This file is that fold: pure, deterministic, no DOM, no fetch, no
// clock of its own.
//
// It lives beside chat-protocol.js rather than inside it because it depends
// on ChessEngine, and chat-protocol.js promises to be dependency-free.
//
// ── Carriers ────────────────────────────────────────────────────────────
//   gm.new   {g, v, c, o}    open a game. v = variant, c = creator's colour
//                            ('w'|'b'), o = invited user (private) or absent
//   gm.join  {g}             take the empty seat
//   gm.move  {g, n, m, f, v} n = ply this move occupies (0-based), m = UCI,
//                            f = FEN of the resulting position. v repeats
//                            the variant so a client that never saw gm.new
//                            still knows what it is adopting
//   gm.res   {g}             resign (a live game -- the opponent wins)
//   gm.cancel{g}             withdraw a game NOBODY joined. Not a resignation:
//                            there is no opponent to hand a win to, and it
//                            never reaches the ladder
//   gm.draw  {g}             offer a draw; the opponent sending it agrees
//   gm.claim {g}             take over a seat abandoned for ABANDON_MS
//   gm.sync  {g, n, r}       "I am at ply n -- is anyone further along?"
//                            r:1 also means "I accept whatever you say",
//                            which is the ONLY thing that un-freezes a game
//                            whose checkpoint disagreed
//   gm.state {g, n, f}       ANY client answering with the position it holds
//   gm.vote  {g, n, m}       ROOM-VS-PLAYER only: vote for the room's move at
//                            ply n. gm.new {r: 1} makes the seat opposite the
//                            creator belong to the whole room instead of one
//                            person, and kv sets how many distinct voters it
//                            takes to commit (default 2)
//
// ── Why each field is there ─────────────────────────────────────────────
//
// `n` makes a move idempotent. Carriers can be delivered twice or out of
// order; a move for a ply we already have is dropped, and one for a ply we
// have not reached yet cannot jump the queue.
//
// `f` is the checkpoint that lets a game outlive the room archive. The
// archive keeps 5000 messages per room and rolls, so a match played across
// days WILL lose its opening moves. A client that never saw them adopts the
// FEN and plays on from there. It also doubles as a consistency check for
// clients that DO have the history: if the sender's FEN disagrees with the
// position we computed, somebody is wrong, and we freeze the game rather
// than silently pick a winner. Adopting theirs would let a hostile client
// rewrite the board; ignoring the mismatch would fork the game invisibly.
//
// ── Why gm.sync exists ──────────────────────────────────────────────────
//
// A forward gm.move resyncs a client that fell behind, but only if a move
// actually arrives. The case it cannot fix is mutual silence: if you missed
// my last move, you think it is my turn and I think it is yours, so NEITHER
// of us sends anything and nothing ever triggers the catch-up. Both boards
// look completely normal. gm.sync is the way out -- ask the room where the
// game is, rather than waiting for a message that is never coming.
//
// Answers are corroborated, not trusted. "Furthest along wins" would reward
// lying -- a bad client claims ply 999 and takes over every time -- so answers
// are grouped by POSITION (not by ply: two clients can be at the same ply with
// different boards, which is exactly the disagreement case) and a position only
// overrules ours once STATE_QUORUM independent clients report it.
//
// Two rules keep that from making things worse:
//   - it never moves us BACKWARDS. If we are at ply 8 and the room agrees on
//     ply 6 because they all missed our last two moves, we are the one holding
//     the information; we answer, we do not adopt.
//   - a disagreement (desync) is only ever settled by quorum. In a two-player
//     game with nobody else around, "the room agrees" would just mean "my
//     opponent says so", which is precisely the injection this guards against.
//     No quorum, no auto-heal: the game stays frozen for a human to settle.
//
// A single SEATED player can still pull us forward when we are merely behind
// and nothing is in dispute -- that cannot invent history, and if they lie the
// next real move disagrees and freezes the game.
//
// ── Time ────────────────────────────────────────────────────────────────
//
// Nothing here reads a local clock. A seat becomes claimable by comparing
// two timestamps FROM THE STREAM, so every client computes the same answer
// no matter how wrong its own clock is — the lesson from the jukebox skew
// bug. Those timestamps come from each user's own slskd, so they differ by
// network latency, which is nothing against a 24h threshold.
//
// Unit-tested in isolation: tests/static/test_chat_games.mjs

(function () {
    'use strict';

    var GID_RE = /^[a-z0-9]{4,16}$/;
    var MAX_GAMES = 40;          // most recently active kept; bounds memory
    var MAX_MOVES = 600;         // no real game is longer; stops a flood
    var ABANDON_MS = 24 * 60 * 60 * 1000;
    // An open challenge nobody took is not a game, it is litter. Derived, not
    // a state change -- see `expired` at the bottom of the fold.
    var OPEN_EXPIRY_MS = 24 * 60 * 60 * 1000;
    // How many tables one player may have open at once (stream-enforced).
    var OPEN_CAP = 3;
    var VOTE_DEFAULT = 2, VOTE_MAX = 9;
    // How many INDEPENDENT clients must report the same position before it can
    // overrule ours. Every SoulSync client in the room folds every game, so the
    // pool is the whole room, not the two players.
    var STATE_QUORUM = 2;

    function _ts(ev) {
        var t = ev && ev.timestamp;
        if (typeof t === 'number' && isFinite(t)) return t;
        var ms = Date.parse(t);
        return isFinite(ms) ? ms : 0;
    }

    // ── Variant adapters ────────────────────────────────────────────────
    // Everything above this line is variant-agnostic. A variant supplies a
    // starting state, a move applier that reports terminal conditions, and
    // an adopt() that validates a position arriving from another machine.
    //
    // Two optional hooks exist for variants that chess and Connect 4 do not
    // need, and both default to today's behaviour when absent:
    //   apply(st, mv, actor)  -- who acted. Battleship needs it: only the
    //                            owner of a board can answer a shot at it.
    //   canAct(st, color)     -- whether `color` may act at all, for phases
    //                            where "the side to move" is the wrong idea
    //                            (both players commit their fleets at once).

    function _engine() { return window.ChessEngine; }

    var CHESS = {
        start: function () {
            var E = _engine();
            if (!E) return null;
            var pos = E.newGame();
            return { pos: pos, history: [E.positionKey(pos)] };
        },
        adopt: function (fen) {
            var E = _engine();
            if (!E) return null;
            var pos = E.fromWireFEN(fen);
            // No history to adopt — a game picked up mid-stream cannot know
            // what was repeated before it arrived, so threefold only counts
            // from here. Better than trusting a repetition claim we cannot
            // check.
            return pos ? { pos: pos, history: [E.positionKey(pos)] } : null;
        },
        fen: function (st) { return _engine().toFEN(st.pos); },
        turn: function (st) { return st.pos.turn; },       // 'w' | 'b'
        apply: function (st, uci) {
            var E = _engine();
            var move = E.uciToMove(st.pos, uci);
            if (!move) return null;                        // illegal or malformed
            var pos = E.makeMove(st.pos, move);
            var history = st.history.concat([E.positionKey(pos)]);
            var s = E.status(pos, history);
            return {
                state: { pos: pos, history: history },
                over: s.over,
                result: s.result,
                reason: s.reason,
                winnerColor: s.winner || '',
            };
        },
    };

    // ── Connect 4 ───────────────────────────────────────────────────────
    //
    // The point of the adapter seam: a whole second game with no change to
    // the lifecycle above. Seats, joining, resigning, draw offers, seat
    // claims, the ladder and the reveal all work already.
    //
    // Position string: 42 cells then a space then the side to move. Index is
    // row * 7 + col with row 0 at the BOTTOM, so a drop is "the lowest empty
    // cell in this column". A move is a single digit, the column.
    //
    // The adopt() validation is doing the same job as the chess engine's
    // forged-en-passant check. A hand-written position could float a disc in
    // mid-air or give one player three extra moves, and every client would
    // accept it identically, so there would be no desync to notice. Because
    // discs are never removed, the whole position is determined by the move
    // counts — which makes it cheap to verify and impossible to fake.
    var C4_COLS = 7, C4_ROWS = 6, C4_CELLS = 42, C4_WIN = 4;

    function _c4At(cells, col, row) {
        if (col < 0 || col >= C4_COLS || row < 0 || row >= C4_ROWS) return null;
        return cells[row * C4_COLS + col] || '';
    }

    function _c4Winner(cells) {
        var dirs = [[1, 0], [0, 1], [1, 1], [1, -1]];
        for (var col = 0; col < C4_COLS; col++) {
            for (var row = 0; row < C4_ROWS; row++) {
                var who = _c4At(cells, col, row);
                if (!who) continue;
                for (var d = 0; d < dirs.length; d++) {
                    var n = 1;
                    while (n < C4_WIN &&
                           _c4At(cells, col + dirs[d][0] * n, row + dirs[d][1] * n) === who) n++;
                    if (n === C4_WIN) return who;
                }
            }
        }
        return '';
    }

    var C4 = {
        start: function () {
            var cells = [];
            for (var i = 0; i < C4_CELLS; i++) cells.push('');
            return { cells: cells, turn: 'w' };
        },
        fen: function (st) {
            return st.cells.map(function (c) { return c || '.'; }).join('') + ' ' + st.turn;
        },
        turn: function (st) { return st.turn; },
        adopt: function (fen) {
            var parts = String(fen || '').split(' ');
            if (parts.length !== 2) return null;
            var body = parts[0], turn = parts[1];
            if (body.length !== C4_CELLS) return null;
            if (turn !== 'w' && turn !== 'b') return null;
            if (!/^[.wb]+$/.test(body)) return null;

            var cells = body.split('').map(function (c) { return c === '.' ? '' : c; });
            var nw = 0, nb = 0;
            for (var i = 0; i < C4_CELLS; i++) {
                if (cells[i] === 'w') nw++;
                else if (cells[i] === 'b') nb++;
            }
            // White moves first, so the counts are either level or one ahead,
            // and they fix whose turn it is exactly.
            if (nw !== nb && nw !== nb + 1) return null;
            if ((nw === nb ? 'w' : 'b') !== turn) return null;
            // No floating discs: above the first gap in a column, all empty.
            for (var col = 0; col < C4_COLS; col++) {
                var seenGap = false;
                for (var row = 0; row < C4_ROWS; row++) {
                    var filled = !!_c4At(cells, col, row);
                    if (!filled) seenGap = true;
                    else if (seenGap) return null;
                }
            }
            return { cells: cells, turn: turn };
        },
        apply: function (st, uci) {
            var col = -1;
            if (typeof uci === 'string' && /^[0-6]$/.test(uci)) col = parseInt(uci, 10);
            if (col < 0) return null;
            var row = -1;
            for (var r = 0; r < C4_ROWS; r++) {
                if (!_c4At(st.cells, col, r)) { row = r; break; }
            }
            if (row < 0) return null;                      // column is full

            var cells = st.cells.slice();
            cells[row * C4_COLS + col] = st.turn;
            var next = { cells: cells, turn: st.turn === 'w' ? 'b' : 'w' };

            var win = _c4Winner(cells);
            if (win) {
                return {
                    state: next, over: true, winnerColor: win,
                    result: win === 'w' ? '1-0' : '0-1', reason: 'four in a row',
                };
            }
            var full = cells.every(function (c) { return !!c; });
            if (full) {
                return { state: next, over: true, winnerColor: '',
                         result: '1/2-1/2', reason: 'board full' };
            }
            return { state: next, over: false, winnerColor: '', result: null, reason: '' };
        },
    };

    // ── Battleship ──────────────────────────────────────────────────────
    //
    // The first game here with hidden information, and the reason chat-hash.js
    // exists. The fold cannot compute whether a shot hit: that depends on a
    // board only its owner can see. So the owner ANSWERS, and the answer is a
    // claim rather than a fact.
    //
    // What makes the claims worth anything is commit-reveal. Before a shot is
    // fired each player publishes hash(salt, board). At the end the loser
    // reveals salt + board, and every client checks two things: that the
    // reveal matches what was committed, and that every answer given over the
    // whole game agrees with the board now revealed. A player who lied about a
    // single "miss" cannot produce a board that fits, so the game is marked
    // cheated and they lose it. Cheating is DETECTED, not prevented -- which is
    // the honest guarantee for a game whose secrets live on the players' own
    // machines.
    //
    // Board string: 100 chars, row-major from a1. '.' is water, '1'..'5' are
    // the five ships. Encoding by ship id (rather than a bare occupied flag)
    // is what lets "sunk" be verified rather than taken on trust.
    var BS_W = 10, BS_H = 10;
    var BS_FLEET = [
        { id: '1', name: 'Carrier', len: 5 },
        { id: '2', name: 'Battleship', len: 4 },
        { id: '3', name: 'Cruiser', len: 3 },
        { id: '4', name: 'Submarine', len: 3 },
        { id: '5', name: 'Destroyer', len: 2 },
    ];
    var BS_CELLS = BS_W * BS_H;

    function _bsCell(s) {
        // "e5" -> index. Column letter then 1-based row, and NOTHING else:
        // a loose parseInt happily read "a1b" as a1.
        if (typeof s !== 'string') return -1;
        var m = /^([a-j])([1-9]|10)$/.exec(s.toLowerCase());
        if (!m) return -1;
        return (parseInt(m[2], 10) - 1) * BS_W + 'abcdefghij'.indexOf(m[1]);
    }
    function _bsName(i) {
        return 'abcdefghij'[i % BS_W] + String(Math.floor(i / BS_W) + 1);
    }

    // Is this a legal fleet? Right pieces, right lengths, each in one straight
    // unbroken line, nothing overlapping. Checked on every reveal, because a
    // board that was never legal makes every answer unfalsifiable.
    function _bsValidBoard(board) {
        if (typeof board !== 'string' || board.length !== BS_CELLS) return false;
        if (!/^[.12345]+$/.test(board)) return false;
        for (var f = 0; f < BS_FLEET.length; f++) {
            var ship = BS_FLEET[f];
            var cells = [];
            for (var i = 0; i < BS_CELLS; i++) if (board[i] === ship.id) cells.push(i);
            if (cells.length !== ship.len) return false;
            var rows = cells.map(function (c) { return Math.floor(c / BS_W); });
            var cols = cells.map(function (c) { return c % BS_W; });
            var sameRow = rows.every(function (r) { return r === rows[0]; });
            var sameCol = cols.every(function (c) { return c === cols[0]; });
            if (!sameRow && !sameCol) return false;
            var line = (sameRow ? cols : rows).slice().sort(function (a2, b2) { return a2 - b2; });
            for (var j = 1; j < line.length; j++) if (line[j] !== line[j - 1] + 1) return false;
        }
        return true;
    }

    // Does `board` agree with every answer its owner gave? `shots` are the
    // cells fired at that board, `results` what the owner claimed each time.
    function _bsAnswersHonest(board, shots, results) {
        var hitCount = {};
        for (var i = 0; i < shots.length; i++) {
            var idx = shots[i];
            var truth = board[idx] !== '.' ? 'hit' : 'miss';
            var claimed = results[i];
            if (claimed === 'sunk') {
                if (truth !== 'hit') return false;
                var ship = board[idx];
                hitCount[ship] = (hitCount[ship] || 0) + 1;
                var len = 0;
                for (var c = 0; c < BS_CELLS; c++) if (board[c] === ship) len++;
                if (hitCount[ship] !== len) return false;   // claimed sunk too early
            } else {
                if (claimed !== truth) return false;
                if (truth === 'hit') {
                    var sh = board[idx];
                    hitCount[sh] = (hitCount[sh] || 0) + 1;
                    var l2 = 0;
                    for (var c2 = 0; c2 < BS_CELLS; c2++) if (board[c2] === sh) l2++;
                    if (hitCount[sh] === l2) return false;  // hid a sinking
                }
            }
        }
        return true;
    }

    // State: {commits:{w,b}, shots:{w:[],b:[]}, results:{w:[],b:[]},
    //         reveal:{w,b}, pending:'' | 'w' | 'b', turn, over, cheat}
    // `pending` is the side that owes an ANSWER; nobody else may act until
    // they give it. shots.w are the cells W has fired (at B's board).
    var BS = {
        start: function () {
            return {
                commits: { w: '', b: '' },
                shots: { w: [], b: [] },
                results: { w: [], b: [] },
                reveal: { w: null, b: null },
                pending: '',
                turn: 'w',
                sunkAll: '',
                cheat: '',
            };
        },
        fen: function (st) { return JSON.stringify(st); },
        adopt: function (fen) {
            var st;
            try { st = JSON.parse(String(fen || '')); } catch (e) { return null; }
            if (!st || typeof st !== 'object') return null;
            if (!st.commits || !st.shots || !st.results) return null;
            if (!Array.isArray(st.shots.w) || !Array.isArray(st.shots.b)) return null;
            if (!Array.isArray(st.results.w) || !Array.isArray(st.results.b)) return null;
            // Answers LAG shots by one while a shot is outstanding — that
            // gap is the whole point of `pending`. Requiring them equal
            // rejected every position between a shot and its answer.
            var gapW = st.shots.w.length - st.results.w.length;
            var gapB = st.shots.b.length - st.results.b.length;
            if (gapW < 0 || gapW > 1 || gapB < 0 || gapB > 1) return null;
            if (gapW + gapB > 1) return null;          // only one shot in the air
            if (st.pending && gapW + gapB !== 1) return null;
            if (!st.pending && gapW + gapB !== 0) return null;
            if (st.shots.w.length + st.shots.b.length > 400) return null;
            if (st.turn !== 'w' && st.turn !== 'b') return null;
            return st;
        },
        turn: function (st) { return st.pending || st.turn; },
        canAct: function (st, color) {
            if (st.over) return false;
            // Setup: both players place at once, each once.
            if (!st.commits.w || !st.commits.b) return !st.commits[color];
            if (st.pending) return color === st.pending;   // an answer is owed
            if (st.sunkAll) return !st.reveal[color];      // reveals are owed
            return color === st.turn;
        },
        apply: function (st, mv, actor) {
            if (!actor || (actor !== 'w' && actor !== 'b')) return null;
            if (st.over) return null;
            var other2 = actor === 'w' ? 'b' : 'w';
            var next = JSON.parse(JSON.stringify(st));
            mv = String(mv || '');

            // c:<digest> — commit a fleet
            if (mv.slice(0, 2) === 'c:') {
                if (st.commits[actor]) return null;               // once only
                var digest = mv.slice(2);
                if (!/^[0-9a-f]{32}$/.test(digest)) return null;
                next.commits[actor] = digest;
                return { state: next, over: false, result: null, reason: '', winnerColor: '' };
            }
            if (!st.commits.w || !st.commits.b) return null;       // no shooting yet

            // s:<cell> — fire
            if (mv.slice(0, 2) === 's:') {
                if (st.pending) return null;                       // answer first
                if (st.sunkAll) return null;
                if (actor !== st.turn) return null;
                var idx = _bsCell(mv.slice(2));
                if (idx < 0) return null;
                if (st.shots[actor].indexOf(idx) >= 0) return null;  // already fired there
                next.shots[actor].push(idx);
                next.pending = other2;                             // they owe an answer
                return { state: next, over: false, result: null, reason: '', winnerColor: '' };
            }

            // r:<hit|miss|sunk> — answer the shot at my board
            if (mv.slice(0, 2) === 'r:') {
                if (st.pending !== actor) return null;
                var res = mv.slice(2);
                if (res !== 'hit' && res !== 'miss' && res !== 'sunk') return null;
                next.results[other2].push(res);
                next.pending = '';
                // Seventeen cells of ship in a fleet; five sinkings ends it.
                var sunk = next.results[other2].filter(function (r) { return r === 'sunk'; }).length;
                if (sunk >= BS_FLEET.length) {
                    next.sunkAll = other2;    // other2 sank actor's whole fleet
                    next.turn = other2;
                    return { state: next, over: false, result: null, reason: '', winnerColor: '' };
                }
                next.turn = actor;            // the answerer fires next; strict alternation
                return { state: next, over: false, result: null, reason: '', winnerColor: '' };
            }

            // v:<salt>:<board> — reveal, and be judged
            if (mv.slice(0, 2) === 'v:') {
                if (!st.sunkAll) return null;                      // nothing to settle yet
                if (st.reveal[actor]) return null;
                var rest = mv.slice(2);
                var cut = rest.indexOf(':');
                if (cut < 0) return null;
                var salt = rest.slice(0, cut), board = rest.slice(cut + 1);
                var H = window.ChatHash;
                if (!H) return null;
                var honest = _bsValidBoard(board) &&
                             H.verify(st.commits[actor], salt, board) &&
                             _bsAnswersHonest(board, st.shots[other2], st.results[other2]);
                next.reveal[actor] = { salt: salt, board: board, honest: honest };
                if (!honest) {
                    next.over = true;
                    next.cheat = actor;
                    return {
                        state: next, over: true, reason: 'cheating',
                        result: actor === 'w' ? '0-1' : '1-0', winnerColor: other2,
                    };
                }
                // BOTH fleets are revealed before the result stands. Checking
                // only the loser would leave the winner's answers unexamined,
                // and the winner is precisely who gains by calling a hit a
                // miss. The game ends when the second honest reveal lands.
                if (next.reveal.w && next.reveal.b) {
                    next.over = true;
                    return {
                        state: next, over: true, reason: 'fleet sunk',
                        result: st.sunkAll === 'w' ? '1-0' : '0-1',
                        winnerColor: st.sunkAll,
                    };
                }
                return { state: next, over: false, result: null, reason: '', winnerColor: '' };
            }
            return null;
        },
    };

    var VARIANTS = { chess: CHESS, connect4: C4, battleship: BS };

    // ── Helpers ─────────────────────────────────────────────────────────

    function _seatOf(game, username) {
        if (game.white === username) return 'w';
        if (game.black === username) return 'b';
        return '';
    }

    function _userForColor(game, color) {
        return color === 'w' ? game.white : game.black;
    }

    function _finish(game, result, reason, winnerColor) {
        game.status = 'over';
        game.result = result;
        game.reason = reason;
        game.winner = winnerColor ? _userForColor(game, winnerColor) : '';
        game.winnerColor = winnerColor || '';
        game.drawOffer = '';
    }

    // ── The fold ────────────────────────────────────────────────────────
    //
    // Returns { games: {id: game}, order: [id, ...] } with `order` most
    // recently active first. Games are plain data — no engine objects leak
    // out except `fen`, so a caller can render without depending on the
    // engine's internals.

    function reduceGames(events, now) {
        var games = {};
        var states = {};          // id -> variant state (kept out of the result)

        (events || []).forEach(function (ev) {
            if (!ev || !ev.p || typeof ev.username !== 'string' || !ev.username) return;
            var p = ev.p;
            var k = p.k;
            if (typeof k !== 'string' || k.slice(0, 3) !== 'gm.') return;

            var gid = String(p.g || '');
            if (!GID_RE.test(gid)) return;
            var at = _ts(ev);
            var user = ev.username;

            // ── Stream-clock expiry ──────────────────────────────────────
            // Wall clocks disagree, but the STREAM is a clock every client
            // shares: message timestamps come from the server, and the fold
            // walks them in the same order everywhere. So 'an open table
            // dies once any later game event sits OPEN_EXPIRY past its
            // creation' is fully deterministic — the smart self-removal a
            // wall-clock rule could never be. Tables in a room that goes
            // completely silent keep their presentation-only `expired` flag
            // (below) until the next event arrives; that's the trade.
            if (at) {
                Object.keys(games).forEach(function (oid) {
                    // The event's OWN table is exempt: a late gm.join is a
                    // human warming the table up, and the stream clock must
                    // not kill it in the same breath that someone sits down.
                    if (oid === gid) return;
                    var og = games[oid];
                    if (og.status === 'open' && og.createdAt &&
                            (at - og.createdAt) >= OPEN_EXPIRY_MS) {
                        og.status = 'over';
                        og.reason = 'expired';
                        og.lastAt = og.createdAt + OPEN_EXPIRY_MS;
                    }
                });
            }

            if (k === 'gm.new') {
                if (games[gid]) return;                      // id already taken
                var variant = String(p.v || 'chess');
                var adapter = VARIANTS[variant];
                if (!adapter) return;
                var st = adapter.start();
                if (!st) return;                             // engine not loaded

                // Creator picks a side; anything else is white, which is
                // also what "creator goes first" means.
                var creatorColor = p.c === 'b' ? 'b' : 'w';
                var invited = typeof p.o === 'string' ? p.o.slice(0, 64) : '';
                // Room-vs-player: the seat opposite the creator belongs to
                // nobody, so it is never filled with a username and gm.join
                // has nothing to take. The room moves by vote instead.
                var roomSeat = p.r ? (creatorColor === 'w' ? 'b' : 'w') : '';
                var voteK = VOTE_DEFAULT;
                if (typeof p.kv === 'number' && isFinite(p.kv)) {
                    voteK = Math.max(1, Math.min(VOTE_MAX, Math.floor(p.kv)));
                }

                // ── The spam cap ─────────────────────────────────────────
                // One player, three open tables, tops: the fourth gm.new
                // expires their OLDEST still-open table in the same breath.
                // Fold-order deterministic, so every client agrees which one
                // died. (Boulder: 'one player with 10 cold open games is
                // spammy' — now the pile can never form.)
                var mineOpen = Object.keys(games).map(function (oid) { return games[oid]; })
                    .filter(function (og) { return og.status === 'open' && og.createdBy === user; })
                    .sort(function (a, b) { return a.createdAt - b.createdAt; });
                while (mineOpen.length >= OPEN_CAP) {
                    var oldest = mineOpen.shift();
                    oldest.status = 'over';
                    oldest.reason = 'expired';
                    oldest.lastAt = at;
                }

                states[gid] = st;
                games[gid] = {
                    id: gid,
                    variant: variant,
                    white: creatorColor === 'w' ? user : '',
                    black: creatorColor === 'b' ? user : '',
                    createdBy: user,
                    createdAt: at,
                    lastAt: at,
                    invited: invited,
                    isPrivate: !!invited,
                    roomSeat: roomSeat,
                    ack: {},             // username -> highest ply they have shown they know
                    syncReq: null,       // latest gm.sync seen for this game
                    answers: {},         // fen -> {n, by:{username:1}} for the live request
                    voteK: voteK,
                    votes: {},           // uci -> distinct voters, current ply
                    voteBy: {},          // username -> uci, current ply
                    fen: adapter.fen(st),
                    startFen: adapter.fen(st),
                    turn: adapter.turn(st),
                    ply: 0,
                    moves: [],
                    status: roomSeat ? 'live' : 'open',
                    result: null,
                    reason: '',
                    winner: '',
                    winnerColor: '',
                    drawOffer: '',
                    partial: false,
                    desync: false,
                    stale: false,
                    claimedAt: 0,
                };
                return;
            }

            var game = games[gid];

            // A move for a game we never saw opened is the archive-rollover
            // case: adopt the checkpoint and play on from there.
            if (!game && k === 'gm.move') {
                var v2 = String(p.v || 'chess');
                var ad2 = VARIANTS[v2];
                if (!ad2) return;
                var adopted = ad2.adopt(String(p.f || ''));
                if (!adopted) return;
                var ply = p.n;
                if (typeof ply !== 'number' || !isFinite(ply) || ply < 0 || ply > MAX_MOVES) return;

                states[gid] = adopted;
                game = games[gid] = {
                    id: gid,
                    variant: v2,
                    // Seats are unknown until someone with the history says
                    // otherwise; the mover's seat is at least derivable.
                    white: '', black: '',
                    createdBy: '', createdAt: at, lastAt: at,
                    invited: '', isPrivate: false,
                    roomSeat: '', ack: {}, syncReq: null, answers: {},
                    voteK: VOTE_DEFAULT, votes: {}, voteBy: {},
                    fen: ad2.fen(adopted),
                    // `moves` below is empty and only collects what arrives
                    // AFTER adoption, so replaying it has to start here --
                    // not from the opening position this client never saw.
                    startFen: ad2.fen(adopted),
                    turn: ad2.turn(adopted),
                    ply: ply + 1,
                    moves: [],
                    status: 'live',
                    result: null, reason: '', winner: '', winnerColor: '',
                    drawOffer: '',
                    partial: true,          // we joined mid-game; no history
                    desync: false,
                    stale: false,
                    claimedAt: 0,
                };
                // The mover held the seat that was to move BEFORE this move,
                // which the adopted FEN no longer shows — so record them on
                // the side that is now waiting.
                var movedColor = ad2.turn(adopted) === 'w' ? 'b' : 'w';
                if (movedColor === 'w') game.white = user; else game.black = user;
                return;
            }

            if (!game) return;
            var adapter2 = VARIANTS[game.variant];
            if (!adapter2) return;
            // Everything else is inert on a frozen game; sync/state are how it
            // gets unfrozen, so they run.
            if (game.desync && k !== 'gm.sync' && k !== 'gm.state') return;

            if (k === 'gm.join') {
                if (game.roomSeat) return;        // that seat belongs to everyone
                if (game.status !== 'open') return;
                if (game.white === user || game.black === user) return;   // no self-play
                if (game.isPrivate && game.invited !== user) return;
                // First join in stream order wins. Stream order is the slskd
                // buffer order, identical on every client, so this needs no
                // tie-break negotiation.
                if (!game.white) game.white = user;
                else if (!game.black) game.black = user;
                else return;
                game.status = 'live';
                game.lastAt = at;
                return;
            }

            if (k === 'gm.move') {
                if (game.status !== 'live') return;
                var turnColor = adapter2.turn(states[gid]);
                var seat = _seatOf(game, user);
                if (!seat && game.partial && !_userForColor(game, turnColor)) {
                    // A game adopted mid-stream has no record of who is
                    // playing, so it learns the seats from whoever moves in
                    // them. A wrong guess cannot stick: the move still has
                    // to be legal, on the right ply, and match the FEN.
                    if (turnColor === 'w') game.white = user; else game.black = user;
                    seat = turnColor;
                }
                if (!seat) return;                            // not a player

                // Catch up BEFORE judging whose turn it is: once we have
                // missed moves our idea of the turn is stale by definition,
                // so checking it first would reject the very message that
                // could fix us. A client that closes the tab for an afternoon
                // comes back behind and slskd never replays what it did not
                // receive, so without this it rejects every later move as
                // off-ply and sits on a stale board forever, silently, while
                // its opponent plays on. The checkpoint exists for this.
                var pn = p.n;
                if (typeof pn === 'number' && isFinite(pn) && pn > game.ply) {
                    var reFen = typeof p.f === 'string' ? p.f : '';
                    var reSt = reFen ? adapter2.adopt(reFen) : null;
                    if (!reSt) return;                        // no way to catch up
                    // Only a seated player can pull the game forward, and only
                    // onto the side the checkpoint says just moved.
                    var reMoved = adapter2.turn(reSt) === 'w' ? 'b' : 'w';
                    if (seat !== reMoved) return;
                    states[gid] = reSt;
                    game.fen = adapter2.fen(reSt);
                    game.turn = adapter2.turn(reSt);
                    game.startFen = game.fen;      // the move list restarts here
                    game.moves = [];
                    game.ply = pn + 1;
                    game.partial = true;           // history is no longer ours to vouch for
                    game.lastAt = at;
                    game.drawOffer = '';
                    game.votes = {}; game.voteBy = {};
                    return;
                }

                if (adapter2.canAct
                        ? !adapter2.canAct(states[gid], seat)
                        : seat !== turnColor) return;             // not your turn
                var n = p.n;
                if (typeof n !== 'number' || !isFinite(n)) return;
                if (n < game.ply) return;                     // a duplicate or a replay
                if (game.ply >= MAX_MOVES) return;

                var uci = String(p.m || '');
                var applied = adapter2.apply(states[gid], uci, seat);
                if (!applied) return;                         // illegal — just dropped

                // The checkpoint has to agree with what we computed. If it
                // does not, one of us is wrong and neither can prove it, so
                // freeze rather than pick.
                var claimedFen = typeof p.f === 'string' ? p.f : '';
                var ourFen = adapter2.fen(applied.state);
                if (claimedFen && claimedFen !== ourFen) {
                    game.desync = true;
                    game.lastAt = at;
                    return;
                }

                states[gid] = applied.state;
                game.ack[user] = Math.max(game.ack[user] || 0, n);
                game.moves.push(uci);
                game.ply++;
                game.fen = ourFen;
                game.turn = adapter2.turn(applied.state);
                game.lastAt = at;
                game.drawOffer = '';                          // any move withdraws it
                game.votes = {};                              // ballots are per-ply
                game.voteBy = {};
                game.answers = {};                            // and so are sync answers
                if (applied.over) {
                    _finish(game, applied.result, applied.reason, applied.winnerColor);
                }
                return;
            }

            if (k === 'gm.sync') {
                // A request changes nothing about the game. It records who
                // asked and how far along they said they were, which is also
                // free proof that they have seen everything up to that ply.
                var sn = p.n;
                if (typeof sn !== 'number' || !isFinite(sn) || sn < 0 || sn > MAX_MOVES) return;
                game.ack[user] = Math.max(game.ack[user] || 0, Math.floor(sn));
                game.syncReq = { by: user, n: Math.floor(sn), at: at, reset: !!p.r };
                return;
            }

            if (k === 'gm.state') {
                var tn = p.n;
                if (typeof tn !== 'number' || !isFinite(tn) || tn < 0 || tn > MAX_MOVES) return;
                tn = Math.floor(tn);
                game.ack[user] = Math.max(game.ack[user] || 0, tn);

                var stFen = String(p.f || '');
                var newSt = adapter2.adopt(stFen);
                if (!newSt) return;               // not even a legal position

                // Tally it. Grouping by position rather than by ply is the
                // point: agreement means agreeing about the BOARD.
                var slot = game.answers[stFen] || (game.answers[stFen] = { n: tn, by: {} });
                slot.by[user] = 1;
                var backers = Object.keys(slot.by).length;
                var quorum = backers >= STATE_QUORUM;

                if (game.desync) {
                    // A disagreement is settled by the room or by a human, and
                    // by nothing else. A human accepting (gm.sync r:1) counts
                    // as the deciding voice, but never from the answerer.
                    var accepted = game.syncReq && game.syncReq.reset &&
                                   game.syncReq.by !== user;
                    if (!quorum && !accepted) return;
                    if (tn < game.ply) return;    // even then, never rewind
                } else if (tn <= game.ply) {
                    return;                       // we are level or ahead: we answer, not adopt
                } else if (!quorum && !_seatOf(game, user)) {
                    // Merely behind: one SEATED player is enough to top us up
                    // (they cannot invent history, and a lie freezes on the
                    // next real move). Anyone else needs corroboration.
                    return;
                }

                states[gid] = newSt;
                game.fen = adapter2.fen(newSt);
                game.turn = adapter2.turn(newSt);
                game.startFen = game.fen;
                game.moves = [];
                game.ply = tn;
                game.partial = true;
                game.desync = false;
                game.syncReq = null;
                game.answers = {};
                game.drawOffer = '';
                game.votes = {}; game.voteBy = {};
                game.lastAt = at;
                return;
            }

            if (k === 'gm.vote') {
                // The room's move is decided by the fold, not by any one
                // client emitting gm.move: the seat has no owner, so there is
                // nobody whose move it would be. The first move to reach
                // voteK DISTINCT current voters is applied right here, which
                // every client computes identically from the same stream.
                if (game.status !== 'live' || !game.roomSeat) return;
                if (adapter2.turn(states[gid]) !== game.roomSeat) return;
                if (p.n !== game.ply) return;                  // a stale ballot
                if (_seatOf(game, user)) return;               // the opponent does not vote
                var vuci = String(p.m || '');
                var vpre = adapter2.apply(states[gid], vuci, game.roomSeat);
                if (!vpre) return;                             // not a legal move

                game.voteBy[user] = vuci;                      // latest vote wins
                var tally = {};
                Object.keys(game.voteBy).forEach(function (u) {
                    tally[game.voteBy[u]] = (tally[game.voteBy[u]] || 0) + 1;
                });
                game.votes = tally;
                game.lastAt = at;
                if (tally[vuci] < game.voteK) return;          // not carried yet

                // Carried: commit it exactly as a played move would be.
                states[gid] = vpre.state;
                game.moves.push(vuci);
                game.ply++;
                game.fen = adapter2.fen(vpre.state);
                game.turn = adapter2.turn(vpre.state);
                game.drawOffer = '';
                game.votes = {};
                game.voteBy = {};
                if (vpre.over) {
                    _finish(game, vpre.result, vpre.reason, vpre.winnerColor);
                }
                return;
            }

            if (k === 'gm.cancel') {
                // Withdrawing before anyone joined. Deliberately NOT a
                // resignation: nobody is sitting opposite to be handed a win,
                // and an unplayed game has no business on the ladder. Once
                // someone has joined you owe them a resignation instead.
                // A room game is 'live' the moment it is created -- there is
                // no opponent to wait for -- so it could never be withdrawn at
                // all, and the only way out was resigning to nobody at ply 0,
                // which recorded a loss for a game that never started. It can
                // be withdrawn while untouched, exactly like an open table.
                var untouched = game.roomSeat && game.status === 'live' && game.ply === 0;
                if (game.status !== 'open' && !untouched) return;
                if (user !== game.createdBy) return;
                game.status = 'over';
                game.result = null;
                game.reason = 'cancelled';
                game.winner = '';
                game.winnerColor = '';
                game.lastAt = at;
                return;
            }

            if (k === 'gm.res') {
                if (game.status !== 'live') return;
                var rseat = _seatOf(game, user);
                if (!rseat) return;
                _finish(game, rseat === 'w' ? '0-1' : '1-0', 'resign', rseat === 'w' ? 'b' : 'w');
                game.lastAt = at;
                return;
            }

            if (k === 'gm.draw') {
                if (game.status !== 'live') return;
                var dseat = _seatOf(game, user);
                if (!dseat) return;
                if (game.drawOffer && game.drawOffer !== user) {
                    _finish(game, '1/2-1/2', 'agreed', '');    // the opponent agreed
                } else {
                    game.drawOffer = user;
                }
                game.lastAt = at;
                return;
            }

            if (k === 'gm.claim') {
                if (game.status !== 'live') return;
                if (game.roomSeat) return;        // the room never goes idle
                if (_seatOf(game, user)) return;               // already playing
                // Both timestamps come from the stream, so this is the same
                // arithmetic on every client regardless of local clocks.
                if (at - game.lastAt < ABANDON_MS) return;
                var idle = adapter2.turn(states[gid]);         // the side that stalled
                if (idle === 'w') game.white = user; else game.black = user;
                game.drawOffer = '';       // it was aimed at whoever left
                game.claimedAt = at;
                game.lastAt = at;
                return;
            }
        });

        // Most recently active first, id as a stable tie-break so two
        // clients never order the lobby differently.
        var order = Object.keys(games).sort(function (a, b) {
            var d = games[b].lastAt - games[a].lastAt;
            if (d) return d;
            return a < b ? -1 : (a > b ? 1 : 0);
        });
        if (order.length > MAX_GAMES) {
            order.slice(MAX_GAMES).forEach(function (id) { delete games[id]; });
            order = order.slice(0, MAX_GAMES);
        }

        // `stale` is presentation only — whether a seat LOOKS abandoned to
        // the person looking at it right now. It never gates a state change;
        // only a gm.claim carrier can move a seat, and that is judged on
        // stream timestamps above.
        if (typeof now === 'number' && isFinite(now)) {
            order.forEach(function (id) {
                var g = games[id];
                g.stale = g.status === 'live' && (now - g.lastAt) >= ABANDON_MS;
                // A table nobody ever sat down at. Presentation only, like
                // `stale`: the creator can still cancel it properly, and a
                // late joiner is not blocked by our clock disagreeing with
                // theirs about the exact minute it went cold.
                g.expired = g.status === 'open' && (now - g.createdAt) >= OPEN_EXPIRY_MS;
            });
        }

        return { games: games, order: order };
    }

    // Whose move is it, as a username? '' when the game is not live or the
    // seat is empty. This is what drives the "your move" badge.
    // Is the room itself on move? Then no single username is "to move".
    function isRoomTurn(game) {
        return !!(game && game.status === 'live' && game.roomSeat &&
                  game.turn === game.roomSeat);
    }

    function toMove(game) {
        if (!game || game.status !== 'live') return '';
        if (isRoomTurn(game)) return '';        // the room, not a person
        // Read the turn the variant recorded, never ply parity. A game
        // adopted mid-stream takes its ply from the wire, so a wrong or
        // hostile `n` would otherwise badge the player who just moved --
        // and parity is a chess assumption that Connect 4 would break.
        return game.turn === 'b' ? game.black : game.white;
    }

    // ── Ratings ─────────────────────────────────────────────────────────
    //
    // A room Elo ladder with no server and no database of record: it is a
    // second fold, this time over the finished games the first fold produced.
    // Everyone starts at 1200.
    //
    // Elo is ORDER-DEPENDENT — rating A before B gives different numbers than
    // B before A — and every client has to land on the same table, so the
    // order cannot come from anything client-local. Games are sorted by
    // finish time rounded to WHOLE SECONDS, then by game id. The rounding
    // matters: finish times come from each user's own slskd, so they differ
    // by network latency, and comparing them at millisecond resolution would
    // let two clients disagree about which of two near-simultaneous games
    // came first. The id tiebreak then makes the order total.
    //
    // Only games this client watched from the opening are rated. A game
    // adopted mid-stream has seats inferred from whoever moved rather than
    // observed from gm.new, and rating a result whose players we deduced is
    // not something the ladder should be doing.
    var ELO_START = 1200;

    function _k(games) { return games < 30 ? 32 : 24; }

    function ratings(foldOut) {
        var out = {};
        var games = (foldOut && foldOut.games) || {};

        var rated = Object.keys(games).map(function (id) { return games[id]; })
            .filter(function (g) {
                return g.status === 'over' && !g.desync && !g.partial &&
                       g.white && g.black && g.white !== g.black && g.result;
            })
            .sort(function (a, b) {
                var as = Math.floor(a.lastAt / 1000), bs = Math.floor(b.lastAt / 1000);
                if (as !== bs) return as - bs;
                return a.id < b.id ? -1 : (a.id > b.id ? 1 : 0);
            });

        function seat(name) {
            return out[name] || (out[name] = {
                name: name, rating: ELO_START, games: 0, wins: 0, losses: 0, draws: 0,
            });
        }

        rated.forEach(function (g) {
            var w = seat(g.white), b = seat(g.black);
            // Score from white's point of view.
            var score = g.result === '1-0' ? 1 : (g.result === '0-1' ? 0 : 0.5);
            var expW = 1 / (1 + Math.pow(10, (b.rating - w.rating) / 400));
            var kw = _k(w.games), kb = _k(b.games);
            var rw = w.rating + kw * (score - expW);
            var rb = b.rating + kb * ((1 - score) - (1 - expW));
            w.rating = rw; b.rating = rb;
            w.games++; b.games++;
            if (score === 1) { w.wins++; b.losses++; }
            else if (score === 0) { w.losses++; b.wins++; }
            else { w.draws++; b.draws++; }
        });

        return Object.keys(out).map(function (n) {
            var r = out[n];
            // Round only on the way out — rounding as we go would compound.
            return {
                name: r.name, rating: Math.round(r.rating), games: r.games,
                wins: r.wins, losses: r.losses, draws: r.draws,
            };
        }).sort(function (a, b) {
            if (b.rating !== a.rating) return b.rating - a.rating;
            if (b.games !== a.games) return b.games - a.games;
            return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
        });
    }

    // What would this move produce? Returns { fen, over } for a legal move
    // and null for anything else, so a caller can build the checkpoint that
    // rides with gm.move without knowing which game it is looking at -- and
    // so an illegal move is caught before it reaches the room rather than
    // being sent and silently dropped by everyone.
    function previewMove(game, uci, actor) {
        if (!game) return null;
        var adapter = VARIANTS[game.variant];
        if (!adapter) return null;
        var st = adapter.adopt(game.fen);
        if (!st) return null;
        var applied = adapter.apply(st, uci, actor);
        if (!applied) return null;
        return { fen: adapter.fen(applied.state), over: !!applied.over };
    }

    window.ChatGames = {
        reduceGames: reduceGames,
        previewMove: previewMove,
        isRoomTurn: isRoomTurn,
        ratings: ratings,
        toMove: toMove,
        STATE_QUORUM: STATE_QUORUM,
        ELO_START: ELO_START,
        ABANDON_MS: ABANDON_MS,
        OPEN_EXPIRY_MS: OPEN_EXPIRY_MS,
        MAX_GAMES: MAX_GAMES,
        MAX_MOVES: MAX_MOVES,
        GID_RE: GID_RE,
        VARIANTS: Object.keys(VARIANTS),
        C4_COLS: C4_COLS,
        C4_ROWS: C4_ROWS,
    };
})();
