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
//   gm.res   {g}             resign
//   gm.draw  {g}             offer a draw; the opponent sending it agrees
//   gm.claim {g}             take over a seat abandoned for ABANDON_MS
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
    var VOTE_DEFAULT = 2, VOTE_MAX = 9;

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
    // Connect 4 slots in here without touching the lifecycle.

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

    var VARIANTS = { chess: CHESS, connect4: C4 };

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
                    roomSeat: '', voteK: VOTE_DEFAULT, votes: {}, voteBy: {},
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

            if (!game || game.desync) return;
            var adapter2 = VARIANTS[game.variant];
            if (!adapter2) return;

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

                if (seat !== turnColor) return;               // not your turn
                var n = p.n;
                if (typeof n !== 'number' || !isFinite(n)) return;
                if (n < game.ply) return;                     // a duplicate or a replay
                if (game.ply >= MAX_MOVES) return;

                var uci = String(p.m || '');
                var applied = adapter2.apply(states[gid], uci);
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
                game.moves.push(uci);
                game.ply++;
                game.fen = ourFen;
                game.turn = adapter2.turn(applied.state);
                game.lastAt = at;
                game.drawOffer = '';                          // any move withdraws it
                game.votes = {};                              // ballots are per-ply
                game.voteBy = {};
                if (applied.over) {
                    _finish(game, applied.result, applied.reason, applied.winnerColor);
                }
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
                var vpre = adapter2.apply(states[gid], vuci);
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
    function previewMove(game, uci) {
        if (!game) return null;
        var adapter = VARIANTS[game.variant];
        if (!adapter) return null;
        var st = adapter.adopt(game.fen);
        if (!st) return null;
        var applied = adapter.apply(st, uci);
        if (!applied) return null;
        return { fen: adapter.fen(applied.state), over: !!applied.over };
    }

    window.ChatGames = {
        reduceGames: reduceGames,
        previewMove: previewMove,
        isRoomTurn: isRoomTurn,
        ratings: ratings,
        toMove: toMove,
        ELO_START: ELO_START,
        ABANDON_MS: ABANDON_MS,
        MAX_GAMES: MAX_GAMES,
        MAX_MOVES: MAX_MOVES,
        GID_RE: GID_RE,
        VARIANTS: Object.keys(VARIANTS),
        C4_COLS: C4_COLS,
        C4_ROWS: C4_ROWS,
    };
})();
