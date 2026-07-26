// Arcade game lifecycle fold (webui/static/chat-games.js).
// Run via: node --test tests/static/test_chat_games.mjs
// (pytest wrapper: tests/test_chat_games_js.py)
//
// A match is protocol carriers in a Soulseek room and nothing else. Every
// client folds the same carriers into the same game with no server, so the
// things worth testing hardest are the ones where clients could DISAGREE or
// where a hostile carrier could move a piece nobody played:
//   - seat assignment when two people race to join
//   - a move from the wrong person, on the wrong ply, or out of turn
//   - a FEN checkpoint that disagrees with the position we computed
//   - a seat claim judged on stream timestamps rather than a local clock
//   - a game adopted mid-stream after the room archive rolled its opening

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const ctx = { window: {} };
vm.createContext(ctx);
for (const f of ['chess-engine.js', 'chat-games.js']) {
    vm.runInContext(readFileSync(resolve(here, '../../webui/static/', f), 'utf-8'), ctx);
}
const G = ctx.window.ChatGames;
const E = ctx.window.ChessEngine;

const T0 = Date.parse('2026-07-25T12:00:00Z');
const iso = ms => new Date(ms).toISOString();

// Build an event the way chat.js does: {username, timestamp, p}.
function ev(username, p, atMs) {
    return { username, timestamp: iso(atMs === undefined ? T0 : atMs), p };
}

// Replay UCI moves into carriers, computing each checkpoint FEN honestly.
function moveEvents(gid, players, ucis, startMs = T0, stepMs = 60000) {
    let pos = E.newGame();
    const out = [];
    ucis.forEach((uci, i) => {
        const mover = pos.turn === 'w' ? players[0] : players[1];
        pos = E.makeMove(pos, E.uciToMove(pos, uci));
        out.push(ev(mover, { k: 'gm.move', g: gid, n: i, m: uci, f: E.toFEN(pos) },
                    startMs + (i + 1) * stepMs));
    });
    return out;
}

const opened = (gid = 'g001', by = 'boulder', extra = {}) =>
    ev(by, { k: 'gm.new', g: gid, v: 'chess', ...extra });
const joined = (gid = 'g001', by = 'kazimir', at = T0 + 1000) =>
    ev(by, { k: 'gm.join', g: gid }, at);

const one = evs => G.reduceGames(evs).games.g001;

describe('opening and joining', () => {
    test('a new game is open with the creator seated as white', () => {
        const g = one([opened()]);
        assert.equal(g.status, 'open');
        assert.equal(g.white, 'boulder');
        assert.equal(g.black, '');
        assert.equal(g.fen, E.START_FEN);
        assert.equal(g.ply, 0);
    });
    test('the creator can choose black, and then moves second', () => {
        const g = one([opened('g001', 'boulder', { c: 'b' }), joined()]);
        assert.equal(g.black, 'boulder');
        assert.equal(g.white, 'kazimir');
        assert.equal(G.toMove(g), 'kazimir');
    });
    test('joining fills the empty seat and starts the game', () => {
        const g = one([opened(), joined()]);
        assert.equal(g.status, 'live');
        assert.equal(g.black, 'kazimir');
        assert.equal(G.toMove(g), 'boulder');
    });
    test('the first join in stream order wins the race', () => {
        // Two people click Join at the same moment. Stream order is the
        // slskd buffer order, which is identical on every client, so this
        // needs no negotiation — but it MUST be first-wins, not last-wins.
        const g = one([opened(), joined('g001', 'kazimir'), joined('g001', 'sella')]);
        assert.equal(g.black, 'kazimir');
    });
    test('you cannot join your own game', () => {
        const g = one([opened(), joined('g001', 'boulder')]);
        assert.equal(g.status, 'open');
        assert.equal(g.black, '');
    });
    test('a private game admits only the invited user', () => {
        const g = one([opened('g001', 'boulder', { o: 'kazimir' }), joined('g001', 'sella')]);
        assert.equal(g.status, 'open');
        assert.equal(g.isPrivate, true);
        const g2 = one([opened('g001', 'boulder', { o: 'kazimir' }), joined('g001', 'kazimir')]);
        assert.equal(g2.status, 'live');
    });
    test('a duplicate game id never overwrites the first', () => {
        const g = one([opened('g001', 'boulder'), opened('g001', 'sella')]);
        assert.equal(g.createdBy, 'boulder');
    });
    test('unknown variants and malformed ids are dropped', () => {
        assert.equal(G.reduceGames([opened('g001', 'b', { v: 'calvinball' })]).order.length, 0);
        for (const bad of ['', 'ab', 'UPPER', 'has space', 'x'.repeat(20), null]) {
            const r = G.reduceGames([ev('b', { k: 'gm.new', g: bad, v: 'chess' })]);
            assert.equal(r.order.length, 0, JSON.stringify(bad));
        }
    });
});

describe('moves', () => {
    const base = [opened(), joined()];
    test('a legal move advances the board and the ply', () => {
        const g = one([...base, ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4'])]);
        assert.equal(g.ply, 1);
        assert.deepEqual(JSON.parse(JSON.stringify(g.moves)), ['e2e4']);
        assert.equal(g.fen.split(' ')[1], 'b');
        assert.equal(G.toMove(g), 'kazimir');
    });
    test('a spectator cannot move', () => {
        const g = one([...base,
            ev('sella', { k: 'gm.move', g: 'g001', n: 0, m: 'e2e4' }, T0 + 5000)]);
        assert.equal(g.ply, 0);
    });
    test('a player cannot move out of turn', () => {
        const g = one([...base,
            ev('kazimir', { k: 'gm.move', g: 'g001', n: 0, m: 'e7e5' }, T0 + 5000)]);
        assert.equal(g.ply, 0);
    });
    test('an illegal move is dropped without breaking the game', () => {
        const g = one([...base,
            ev('boulder', { k: 'gm.move', g: 'g001', n: 0, m: 'e2e5' }, T0 + 5000),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4'])]);
        assert.equal(g.ply, 1);
        assert.equal(g.desync, false);
    });
    test('a replayed move is idempotent — the ply number absorbs it', () => {
        const mv = moveEvents('g001', ['boulder', 'kazimir'], ['e2e4']);
        const g = one([...base, ...mv, ...mv, ...mv]);
        assert.equal(g.ply, 1);
        assert.equal(g.moves.length, 1);
    });
    test('a move for a ply we have not reached cannot jump the queue', () => {
        const g = one([...base,
            ev('boulder', { k: 'gm.move', g: 'g001', n: 4, m: 'e2e4' }, T0 + 5000)]);
        assert.equal(g.ply, 0);
    });
    test('a full opening plays through in order', () => {
        const ucis = ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5', 'a7a6'];
        const g = one([...base, ...moveEvents('g001', ['boulder', 'kazimir'], ucis)]);
        assert.equal(g.ply, 6);
        assert.equal(G.toMove(g), 'boulder');
    });
});

describe('the FEN checkpoint', () => {
    test('a checkpoint that disagrees with us freezes the game', () => {
        // Neither side can prove the other wrong, so we refuse to pick.
        // Adopting theirs would let a hostile client rewrite the board;
        // ignoring it would fork the game with nobody noticing.
        const g = one([opened(), joined(),
            ev('boulder', { k: 'gm.move', g: 'g001', n: 0, m: 'e2e4',
                            f: '4k3/8/8/8/8/8/8/4K2R b - - 0 1' }, T0 + 5000)]);
        assert.equal(g.desync, true);
        assert.equal(g.ply, 0, 'the bogus move is not applied');
    });
    test('a frozen game ignores everything that follows', () => {
        const g = one([opened(), joined(),
            ev('boulder', { k: 'gm.move', g: 'g001', n: 0, m: 'e2e4',
                            f: '4k3/8/8/8/8/8/8/4K2R b - - 0 1' }, T0 + 5000),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4', 'e7e5'])]);
        assert.equal(g.desync, true);
        assert.equal(g.ply, 0);
    });
    test('a move with no checkpoint at all is still accepted', () => {
        // The field is a cross-check, not a requirement; an older client
        // that omits it should not be locked out.
        const g = one([opened(), joined(),
            ev('boulder', { k: 'gm.move', g: 'g001', n: 0, m: 'e2e4' }, T0 + 5000)]);
        assert.equal(g.ply, 1);
        assert.equal(g.desync, false);
    });
});

describe('adoption — surviving the rolling archive', () => {
    // The room archive keeps 5000 messages and rolls, so a game played over
    // days loses its opening. A client that never saw gm.new picks the game
    // up from the checkpoint instead of showing nothing.
    const midFen = 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3';

    test('a move for an unseen game adopts the position', () => {
        const g = one([ev('kazimir', { k: 'gm.move', g: 'g001', v: 'chess', n: 7,
                                       m: 'e7e5', f: midFen }, T0)]);
        assert.ok(g);
        assert.equal(g.partial, true, 'flagged: we cannot vouch for the history');
        assert.equal(g.fen, midFen);
        assert.equal(g.ply, 8);
        assert.equal(g.status, 'live');
        assert.equal(g.black, 'kazimir', 'the mover held the side now waiting');
    });
    test('an adopted game records where IT started, not the opening', () => {
        // `moves` only collects what arrives after adoption, so without this
        // a renderer replaying them from the standard start position would
        // claim the game opened with them.
        const g = one([ev('kazimir', { k: 'gm.move', g: 'g001', v: 'chess', n: 7,
                                       m: 'e7e5', f: midFen }, T0)]);
        assert.equal(g.startFen, midFen);
        assert.deepEqual(JSON.parse(JSON.stringify(g.moves)), []);
    });
    test('a game opened normally starts from the standard position', () => {
        assert.equal(one([opened()]).startFen, E.START_FEN);
    });
    test('an adopted game learns the other seat from a legal move', () => {
        // Without this the opponent's next move would be rejected as
        // "not a player" and the game would stall forever.
        const g = one([
            ev('kazimir', { k: 'gm.move', g: 'g001', v: 'chess', n: 7, m: 'e7e5', f: midFen }, T0),
            ev('boulder', { k: 'gm.move', g: 'g001', n: 8, m: 'g1f3' }, T0 + 1000)]);
        assert.equal(g.white, 'boulder');
        assert.equal(g.ply, 9);
    });
    test('a forged checkpoint is refused at the door', () => {
        // The ep square is the dangerous one: it makes a pawn capture onto
        // an empty square, identically on every client.
        const forged = '4k3/8/8/8/8/8/4P3/4K3 w - d3 0 1';
        assert.equal(G.reduceGames([
            ev('mallory', { k: 'gm.move', g: 'g001', v: 'chess', n: 3, m: 'e2e4', f: forged }, T0),
        ]).order.length, 0);
    });
    test('a nonsense checkpoint adopts nothing', () => {
        for (const f of ['', 'garbage', '4k3/8/8/8/8/8/8/8 w - - 0 1']) {
            assert.equal(G.reduceGames([
                ev('m', { k: 'gm.move', g: 'g001', v: 'chess', n: 1, m: 'e2e4', f }, T0),
            ]).order.length, 0, JSON.stringify(f));
        }
    });
});

describe('catching up after missing moves', () => {
    // THE async failure mode. slskd only receives room messages while it is
    // joined and never replays what it missed, so a client that closes the
    // tab for an afternoon comes back behind. Before the resync it rejected
    // every later move for being off-ply and sat on a stale board forever,
    // silently, while its opponent played on.
    const players = ['boulder', 'kazimir'];
    const ucis = ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5', 'a7a6'];
    const full = [opened(), joined(), ...moveEvents('g001', players, ucis)];
    const caughtUp = one(full);

    test('a client that missed the middle converges on the same board', () => {
        const behind = one([...full.slice(0, 4), full[full.length - 1]]);
        assert.equal(behind.fen, caughtUp.fen);
        assert.equal(behind.ply, caughtUp.ply);
        assert.equal(behind.partial, true, 'flagged: the history is no longer ours');
    });
    test('it keeps playing normally afterwards', () => {
        // The point of catching up is being able to move again.
        const behind = [...full.slice(0, 4), full[full.length - 1]];
        let pos = E.fromFEN(caughtUp.fen);
        const mv = E.uciToMove(pos, 'b5c6');
        pos = E.makeMove(pos, mv);
        const g = one([...behind, ev('boulder',
            { k: 'gm.move', g: 'g001', n: 6, m: 'b5c6', f: E.toFEN(pos) }, T0 + 999999)]);
        assert.equal(g.ply, 7);
        assert.equal(g.fen, E.toFEN(pos));
    });
    test('the move list restarts at the catch-up point rather than lying', () => {
        const behind = one([...full.slice(0, 4), full[full.length - 1]]);
        assert.deepEqual(JSON.parse(JSON.stringify(behind.moves)), [],
                         'we did not witness these moves');
        assert.equal(behind.startFen, behind.fen, 'so the list starts here');
    });
    test('only a seated player can pull the game forward', () => {
        // Otherwise anyone in the room could fast-forward a game to a
        // position of their choosing.
        const spoofed = { ...full[full.length - 1] };
        spoofed.username = 'mallory';
        assert.equal(one([...full.slice(0, 4), spoofed]).ply, 2, 'spectator refused');
    });
    test('and only onto the side the checkpoint says just moved', () => {
        // boulder is white; a checkpoint showing black to move means WHITE
        // moved, so a claim from black is inconsistent.
        const wrongSeat = { ...full[full.length - 1] };
        wrongSeat.username = 'boulder';       // white claiming black's move
        assert.equal(one([...full.slice(0, 4), wrongSeat]).ply, 2);
    });
    test('a forward move with no checkpoint cannot resync', () => {
        const noFen = ev('kazimir', { k: 'gm.move', g: 'g001', n: 5, m: 'a7a6' }, T0 + 999);
        assert.equal(one([...full.slice(0, 4), noFen]).ply, 2);
    });
    test('a forged checkpoint cannot resync either', () => {
        const forged = ev('kazimir', { k: 'gm.move', g: 'g001', n: 5, m: 'a7a6',
            f: '4k3/8/8/8/8/8/4P3/4K3 w - d3 0 1' }, T0 + 999);
        assert.equal(one([...full.slice(0, 4), forged]).ply, 2);
    });
    test('a replayed older move still cannot rewind the board', () => {
        const g = one([...full, full[2], full[3]]);
        assert.equal(g.ply, caughtUp.ply);
        assert.equal(g.fen, caughtUp.fen);
    });
    test('Connect 4 catches up the same way', () => {
        const evs = [ev('boulder', { k: 'gm.new', g: 'c001', v: 'connect4' }),
                     ev('kazimir', { k: 'gm.join', g: 'c001' }, T0 + 1000)];
        const heights = new Array(7).fill(0);
        const cells = new Array(42).fill('.');
        [3, 3, 4, 2, 4, 4].forEach((col, i) => {
            const who = i % 2 === 0 ? 'w' : 'b';
            cells[heights[col] * 7 + col] = who;
            heights[col]++;
            evs.push(ev(i % 2 === 0 ? 'boulder' : 'kazimir',
                { k: 'gm.move', g: 'c001', v: 'connect4', n: i, m: String(col),
                  f: cells.join('') + ' ' + (who === 'w' ? 'b' : 'w') }, T0 + 2000 + i));
        });
        const whole = G.reduceGames(evs).games.c001;
        const behind = G.reduceGames([...evs.slice(0, 4), evs[evs.length - 1]]).games.c001;
        assert.equal(behind.fen, whole.fen);
        assert.equal(behind.ply, whole.ply);
    });
});

describe('gm.sync — breaking the mutual-wait deadlock', () => {
    // The case a forward gm.move cannot fix: if you missed my last move, you
    // think it is my turn and I think it is yours, so NEITHER of us sends
    // anything and the catch-up never triggers. Both boards look normal.
    const players = ['boulder', 'kazimir'];
    const full = [opened(), joined(),
        ...moveEvents('g001', players, ['e2e4', 'e7e5', 'g1f3'])];
    const behind = full.slice(0, 4);             // missed the third move
    const ahead = one(full);

    test('the deadlock is real: each waits on the other', () => {
        const b = one(behind);
        assert.equal(G.toMove(ahead), 'kazimir');
        assert.equal(G.toMove(b), 'boulder');
        assert.equal(b.desync, false, 'and neither is flagged as wrong');
    });
    test('a state answer pulls the stale client forward', () => {
        const g = one([...behind,
            ev('kazimir', { k: 'gm.sync', g: 'g001', n: 2 }, T0 + 900000),
            ev('boulder', { k: 'gm.state', g: 'g001', n: ahead.ply, f: ahead.fen },
               T0 + 900001)]);
        assert.equal(g.fen, ahead.fen);
        assert.equal(g.ply, ahead.ply);
        assert.equal(G.toMove(g), 'kazimir', 'and now it knows it is its own move');
    });
    test('only a seated player may state the position', () => {
        const g = one([...behind,
            ev('sella', { k: 'gm.state', g: 'g001', n: ahead.ply, f: ahead.fen }, T0 + 9e5)]);
        assert.equal(g.ply, 2, 'a spectator cannot restate the board');
    });
    test('a forged position is refused even from a player', () => {
        const g = one([...behind,
            ev('boulder', { k: 'gm.state', g: 'g001', n: 5,
                            f: '4k3/8/8/8/8/8/4P3/4K3 w - d3 0 1' }, T0 + 9e5)]);
        assert.equal(g.ply, 2);
    });
    test('a state we are already past is ignored', () => {
        const g = one([...full,
            ev('boulder', { k: 'gm.state', g: 'g001', n: 1, f: E.START_FEN }, T0 + 9e5)]);
        assert.equal(g.ply, ahead.ply, 'no rewind');
        assert.equal(g.fen, ahead.fen);
    });
    test('a sync request on its own changes nothing', () => {
        const g = one([...full, ev('kazimir', { k: 'gm.sync', g: 'g001', n: 0 }, T0 + 9e5)]);
        assert.equal(g.fen, ahead.fen);
        assert.equal(g.ply, ahead.ply);
        assert.equal(g.syncReq.by, 'kazimir', 'but it is recorded so a client can answer');
    });
    test('malformed requests and answers are dropped', () => {
        for (const bad of [undefined, -1, 1e9, 'two', null]) {
            assert.equal(one([...full, ev('kazimir',
                { k: 'gm.sync', g: 'g001', n: bad }, T0 + 9e5)]).syncReq, null, String(bad));
        }
    });
});

describe('un-freezing a desynced game', () => {
    // A checkpoint disagreement freezes the game on purpose, and nothing
    // automatic may resolve it -- otherwise whoever re-broadcast last would
    // simply win. Only a human saying "I accept yours" breaks the tie.
    const frozen = [opened(), joined(),
        ev('boulder', { k: 'gm.move', g: 'g001', n: 0, m: 'e2e4',
                        f: '4k3/8/8/8/8/8/8/4K2R b - - 0 1' }, T0 + 5000)];
    let good = E.newGame();
    good = E.makeMove(good, E.uciToMove(good, 'e2e4'));

    test('it really is frozen', () => {
        assert.equal(one(frozen).desync, true);
    });
    test('an ordinary state answer does NOT un-freeze it', () => {
        const g = one([...frozen,
            ev('boulder', { k: 'gm.state', g: 'g001', n: 1, f: E.toFEN(good) }, T0 + 6000)]);
        assert.equal(g.desync, true, 'automatic sync never settles a disagreement');
    });
    test('accepting their position does un-freeze it', () => {
        const g = one([...frozen,
            ev('kazimir', { k: 'gm.sync', g: 'g001', n: 0, r: 1 }, T0 + 6000),
            ev('boulder', { k: 'gm.state', g: 'g001', n: 1, f: E.toFEN(good) }, T0 + 6001)]);
        assert.equal(g.desync, false);
        assert.equal(g.fen, E.toFEN(good));
        assert.equal(g.ply, 1);
    });
    test('you cannot answer your own acceptance', () => {
        const g = one([...frozen,
            ev('boulder', { k: 'gm.sync', g: 'g001', n: 0, r: 1 }, T0 + 6000),
            ev('boulder', { k: 'gm.state', g: 'g001', n: 1, f: E.toFEN(good) }, T0 + 6001)]);
        assert.equal(g.desync, true);
    });
    test('moves stay inert while frozen', () => {
        const g = one([...frozen,
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4', 'e7e5'])]);
        assert.equal(g.desync, true);
        assert.equal(g.ply, 0);
    });
});

describe('quorum — the room outvotes a single voice', () => {
    // Every SoulSync client in the room folds every game, so the pool that can
    // answer is the whole room. That only helps if answers are corroborated:
    // "furthest along wins" would reward lying outright.
    const players = ['boulder', 'kazimir'];
    const full = [opened(), joined(),
        ...moveEvents('g001', players, ['e2e4', 'e7e5', 'g1f3'])];
    const behind = full.slice(0, 4);
    const ahead = one(full);
    const st = (by, n, f, at) => ev(by, { k: 'gm.state', g: 'g001', n: n, f: f }, at);

    // A legal but WRONG position — a liar's board.
    let fake = E.newGame();
    ['d2d4', 'd7d5', 'c1f4'].forEach(u => { fake = E.makeMove(fake, E.uciToMove(fake, u)); });

    test('one non-player alone cannot move our board', () => {
        const g = one([...behind, st('sella', ahead.ply, ahead.fen, T0 + 9e5)]);
        assert.equal(g.ply, 2, 'a single unseated voice is not enough');
    });
    test('two independent clients agreeing does move it', () => {
        const g = one([...behind,
            st('sella', ahead.ply, ahead.fen, T0 + 9e5),
            st('lain', ahead.ply, ahead.fen, T0 + 9e5 + 1)]);
        assert.equal(g.fen, ahead.fen);
        assert.equal(g.ply, ahead.ply);
    });
    test('the same client repeating itself is still one voice', () => {
        const g = one([...behind,
            st('sella', ahead.ply, ahead.fen, T0 + 9e5),
            st('sella', ahead.ply, ahead.fen, T0 + 9e5 + 1),
            st('sella', ahead.ply, ahead.fen, T0 + 9e5 + 2)]);
        assert.equal(g.ply, 2, 'agreement means DISTINCT clients');
    });
    test('a lone liar claiming a huge ply is ignored', () => {
        // "Furthest wins" would hand the game to whoever exaggerates most.
        const g = one([...behind, st('mallory', 400, E.toFEN(fake), T0 + 9e5)]);
        assert.equal(g.ply, 2);
        assert.equal(g.fen, one(behind).fen);
    });
    test('a liar cannot outrank a corroborated position by claiming more', () => {
        const g = one([...behind,
            st('mallory', 400, E.toFEN(fake), T0 + 9e5),
            st('sella', ahead.ply, ahead.fen, T0 + 9e5 + 1),
            st('lain', ahead.ply, ahead.fen, T0 + 9e5 + 2)]);
        assert.equal(g.fen, ahead.fen, 'two honest clients beat one loud one');
    });
    test('agreement is on the POSITION, not the ply number', () => {
        // Two clients at the same ply with different boards agree about
        // nothing, and neither should carry.
        let other = E.newGame();
        ['d2d4', 'd7d5', 'g1f3'].forEach(u => { other = E.makeMove(other, E.uciToMove(other, u)); });
        const g = one([...behind,
            st('sella', 3, ahead.fen, T0 + 9e5),
            st('lain', 3, E.toFEN(other), T0 + 9e5 + 1)]);
        assert.equal(g.ply, 2);
    });
    test('a seated player alone can still top us up when nothing is disputed', () => {
        // They cannot invent history, and a lie freezes on the next real move.
        const g = one([...behind, st('boulder', ahead.ply, ahead.fen, T0 + 9e5)]);
        assert.equal(g.fen, ahead.fen);
    });
    test('the room never drags us backwards', () => {
        // We are at ply 3; the room agrees on ply 1 because they missed our
        // last moves. Those moves are real and must not be deleted.
        let early = E.newGame();
        early = E.makeMove(early, E.uciToMove(early, 'e2e4'));
        const g = one([...full,
            st('sella', 1, E.toFEN(early), T0 + 9e5),
            st('lain', 1, E.toFEN(early), T0 + 9e5 + 1),
            st('kazimir', 1, E.toFEN(early), T0 + 9e5 + 2)]);
        assert.equal(g.ply, ahead.ply, 'we hold the information; we answer, not adopt');
        assert.equal(g.fen, ahead.fen);
    });
    test('a landed move clears outstanding answers', () => {
        // Otherwise a half-formed quorum could still carry a position several
        // moves after it stopped being true.
        const partial = one([...behind, st('sella', ahead.ply, ahead.fen, T0 + 9e5)]);
        assert.equal(Object.keys(partial.answers).length, 1, 'one answer pending');
        const moved = one([...full, st('sella', ahead.ply, ahead.fen, T0 + 9e5),
            ...moveEvents('g001', players, ['e2e4', 'e7e5', 'g1f3', 'b8c6']).slice(3)]);
        assert.equal(Object.keys(moved.answers).length, 0, 'the move wiped them');
    });
});

describe('quorum settles a frozen game without a human', () => {
    const frozen = [opened(), joined(),
        ev('boulder', { k: 'gm.move', g: 'g001', n: 0, m: 'e2e4',
                        f: '4k3/8/8/8/8/8/8/4K2R b - - 0 1' }, T0 + 5000)];
    let good = E.newGame();
    good = E.makeMove(good, E.uciToMove(good, 'e2e4'));
    const st = (by, at) => ev(by, { k: 'gm.state', g: 'g001', n: 1, f: E.toFEN(good) }, at);

    test('one voice still cannot settle it', () => {
        assert.equal(one([...frozen, st('boulder', T0 + 6000)]).desync, true);
    });
    test('but the room can', () => {
        const g = one([...frozen, st('boulder', T0 + 6000), st('sella', T0 + 6001)]);
        assert.equal(g.desync, false, 'consensus is not a guess');
        assert.equal(g.fen, E.toFEN(good));
    });
    test('a human accepting still works when there is no room to ask', () => {
        const g = one([...frozen,
            ev('kazimir', { k: 'gm.sync', g: 'g001', n: 0, r: 1 }, T0 + 6000),
            st('boulder', T0 + 6001)]);
        assert.equal(g.desync, false);
    });
});

describe('acknowledgement — free, from carriers we already send', () => {
    // "Has my opponent seen my move" needs no extra round trip: any carrier
    // they send proves what they knew at the time.
    test('a move proves receipt of everything before it', () => {
        const g = one([opened(), joined(),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4', 'e7e5'])]);
        assert.equal(g.ack.kazimir, 1, 'kazimir answered at ply 1');
        assert.equal(g.ack.boulder, 0);
    });
    test('a sync request proves it too', () => {
        const g = one([opened(), joined(),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4']),
            ev('kazimir', { k: 'gm.sync', g: 'g001', n: 1 }, T0 + 9e5)]);
        assert.equal(g.ack.kazimir, 1, 'they told us how far along they are');
    });
    test('acknowledgement never goes backwards', () => {
        const g = one([opened(), joined(),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4', 'e7e5']),
            ev('kazimir', { k: 'gm.sync', g: 'g001', n: 0 }, T0 + 9e5)]);
        assert.equal(g.ack.kazimir, 1);
    });
});

describe('endings', () => {
    test('checkmate ends the game and names the winner', () => {
        // Fool's mate: black delivers it on ply 3.
        const g = one([opened(), joined(),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['f2f3', 'e7e5', 'g2g4', 'd8h4'])]);
        assert.equal(g.status, 'over');
        assert.equal(g.reason, 'checkmate');
        assert.equal(g.result, '0-1');
        assert.equal(g.winner, 'kazimir');
    });
    test('nothing lands after the game is over', () => {
        const mate = moveEvents('g001', ['boulder', 'kazimir'], ['f2f3', 'e7e5', 'g2g4', 'd8h4']);
        const g = one([opened(), joined(), ...mate,
            ev('boulder', { k: 'gm.move', g: 'g001', n: 4, m: 'e1f2' }, T0 + 900000),
            ev('boulder', { k: 'gm.res', g: 'g001' }, T0 + 900001)]);
        assert.equal(g.ply, 4);
        assert.equal(g.result, '0-1', 'a late resignation cannot rewrite the result');
    });
    test('resignation hands the win to the opponent', () => {
        const g = one([opened(), joined(), ev('boulder', { k: 'gm.res', g: 'g001' }, T0 + 5000)]);
        assert.equal(g.status, 'over');
        assert.equal(g.reason, 'resign');
        assert.equal(g.result, '0-1');
        assert.equal(g.winner, 'kazimir');
    });
    test('a spectator cannot resign someone else\'s game', () => {
        const g = one([opened(), joined(), ev('sella', { k: 'gm.res', g: 'g001' }, T0 + 5000)]);
        assert.equal(g.status, 'live');
    });
    test('a draw needs both players', () => {
        const offer = [opened(), joined(), ev('boulder', { k: 'gm.draw', g: 'g001' }, T0 + 1)];
        assert.equal(one(offer).status, 'live', 'one offer is not agreement');
        assert.equal(one(offer).drawOffer, 'boulder');
        // The same player repeating themselves is still not agreement.
        assert.equal(one([...offer, ev('boulder', { k: 'gm.draw', g: 'g001' }, T0 + 2)]).status, 'live');
        const agreed = one([...offer, ev('kazimir', { k: 'gm.draw', g: 'g001' }, T0 + 3)]);
        assert.equal(agreed.status, 'over');
        assert.equal(agreed.reason, 'agreed');
        assert.equal(agreed.result, '1/2-1/2');
        assert.equal(agreed.winner, '');
    });
    test('a move withdraws a standing draw offer', () => {
        const g = one([opened(), joined(),
            ev('boulder', { k: 'gm.draw', g: 'g001' }, T0 + 1),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4'])]);
        assert.equal(g.drawOffer, '');
        assert.equal(g.status, 'live');
    });
    test('a spectator cannot force a draw by echoing an offer', () => {
        const g = one([opened(), joined(),
            ev('boulder', { k: 'gm.draw', g: 'g001' }, T0 + 1),
            ev('sella', { k: 'gm.draw', g: 'g001' }, T0 + 2)]);
        assert.equal(g.status, 'live');
    });
});

describe('leaving — withdrawing vs resigning', () => {
    // Setting up a table and getting bored of it is not losing. Resigning
    // hands the opponent a win, which is nonsense when nobody ever sat down.
    test('the creator can withdraw a game nobody joined', () => {
        const g = one([opened(), ev('boulder', { k: 'gm.cancel', g: 'g001' }, T0 + 5000)]);
        assert.equal(g.status, 'over');
        assert.equal(g.reason, 'cancelled');
        assert.equal(g.result, null, 'no result: nobody played');
        assert.equal(g.winner, '');
    });
    test('a withdrawn game never reaches the ladder', () => {
        const evs = [opened(), ev('boulder', { k: 'gm.cancel', g: 'g001' }, T0 + 5000)];
        assert.deepEqual(JSON.parse(JSON.stringify(G.ratings(G.reduceGames(evs)))), []);
    });
    test('once someone has joined you owe them a resignation', () => {
        const g = one([opened(), joined(),
            ev('boulder', { k: 'gm.cancel', g: 'g001' }, T0 + 5000)]);
        assert.equal(g.status, 'live', 'withdrawing is refused on a live game');
        const r = one([opened(), joined(), ev('boulder', { k: 'gm.res', g: 'g001' }, T0 + 5000)]);
        assert.equal(r.winner, 'kazimir', 'resigning still hands over the win');
    });
    test('only the creator can withdraw their table', () => {
        const g = one([opened(), ev('sella', { k: 'gm.cancel', g: 'g001' }, T0 + 5000)]);
        assert.equal(g.status, 'open');
    });
    test('a withdrawn table cannot be joined afterwards', () => {
        const g = one([opened(), ev('boulder', { k: 'gm.cancel', g: 'g001' }, T0 + 5000),
            joined('g001', 'kazimir', T0 + 6000)]);
        assert.equal(g.black, '');
        assert.equal(g.status, 'over');
    });
    test('a table nobody sat at reads as gone cold', () => {
        const DAY = G.OPEN_EXPIRY_MS;
        assert.equal(G.reduceGames([opened()], T0 + DAY - 1000).games.g001.expired, false);
        assert.equal(G.reduceGames([opened()], T0 + DAY + 1000).games.g001.expired, true);
        // Presentation only — a late joiner is not blocked by our clock.
        const late = one([opened(), joined('g001', 'kazimir', T0 + DAY + 5000)]);
        assert.equal(late.status, 'live', 'they can still sit down');
    });
});

describe('abandoned seats', () => {
    const DAY = G.ABANDON_MS;
    const base = [opened(), joined(), ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4'])];
    const lastAt = T0 + 60000;

    test('a seat cannot be claimed before the timeout', () => {
        const g = one([...base, ev('sella', { k: 'gm.claim', g: 'g001' }, lastAt + DAY - 1000)]);
        assert.equal(g.black, 'kazimir');
    });
    test('after the timeout the idle seat is claimable', () => {
        const g = one([...base, ev('sella', { k: 'gm.claim', g: 'g001' }, lastAt + DAY + 1000)]);
        assert.equal(g.black, 'sella', 'kazimir was to move and had stalled');
        assert.equal(g.white, 'boulder', 'the player who WAS showing up keeps their seat');
        assert.equal(g.status, 'live', 'the game continues rather than being cancelled');
    });
    test('the claim is judged on stream timestamps, not a local clock', () => {
        // Same events, wildly different "now" — the outcome must not move.
        // This is the jukebox skew lesson: a client with a wrong clock must
        // not compute a different game state from everyone else.
        const evs = [...base, ev('sella', { k: 'gm.claim', g: 'g001' }, lastAt + DAY + 1000)];
        for (const now of [0, T0, T0 + 5 * DAY, Date.parse('2050-01-01T00:00:00Z')]) {
            assert.equal(G.reduceGames(evs, now).games.g001.black, 'sella', `now=${now}`);
        }
    });
    test('a seated player cannot claim their own game', () => {
        const g = one([...base, ev('boulder', { k: 'gm.claim', g: 'g001' }, lastAt + DAY + 1000)]);
        assert.equal(g.black, 'kazimir');
    });
    test('the claimer then plays from the position as it stood', () => {
        const g = one([...base,
            ev('sella', { k: 'gm.claim', g: 'g001' }, lastAt + DAY + 1000),
            ev('sella', { k: 'gm.move', g: 'g001', n: 1, m: 'e7e5' }, lastAt + DAY + 2000)]);
        assert.equal(g.ply, 2);
        assert.equal(g.black, 'sella');
    });
    test('an over game cannot be claimed', () => {
        const g = one([opened(), joined(),
            ev('boulder', { k: 'gm.res', g: 'g001' }, T0 + 5000),
            ev('sella', { k: 'gm.claim', g: 'g001' }, T0 + 5000 + DAY + 1)]);
        assert.equal(g.black, 'kazimir');
    });
    test('`stale` is presentation only and never moves a seat', () => {
        const fresh = G.reduceGames(base, lastAt + 1000).games.g001;
        assert.equal(fresh.stale, false);
        const old = G.reduceGames(base, lastAt + DAY + 1000).games.g001;
        assert.equal(old.stale, true);
        assert.equal(old.black, 'kazimir', 'looking stale changes nothing on its own');
    });
});

describe('bounds and hostile input', () => {
    test('non-game carriers are ignored', () => {
        const r = G.reduceGames([ev('b', { k: 'jbx.vote', o: 'abc' }), ev('b', { k: 'poll.end' })]);
        assert.equal(r.order.length, 0);
    });
    test('events with no username or no payload are ignored', () => {
        const r = G.reduceGames([
            { p: { k: 'gm.new', g: 'g001', v: 'chess' } },
            { username: 'b' },
            { username: '', p: { k: 'gm.new', g: 'g2', v: 'chess' } },
            null, undefined, 42,
        ]);
        assert.equal(r.order.length, 0);
    });
    test('the tracked game count is capped', () => {
        const evs = [];
        for (let i = 0; i < G.MAX_GAMES + 15; i++) {
            evs.push(ev('boulder', { k: 'gm.new', g: 'g' + String(i).padStart(4, '0'), v: 'chess' },
                        T0 + i * 1000));
        }
        const r = G.reduceGames(evs);
        assert.equal(r.order.length, G.MAX_GAMES);
        assert.equal(Object.keys(r.games).length, G.MAX_GAMES, 'dropped games are really gone');
        assert.ok(r.order.includes('g0054'), 'the most recent survive');
    });
    test('the lobby order is most-recent-first and stable across clients', () => {
        const evs = [
            ev('a', { k: 'gm.new', g: 'aaaa', v: 'chess' }, T0),
            ev('b', { k: 'gm.new', g: 'bbbb', v: 'chess' }, T0 + 2000),
            ev('c', { k: 'gm.new', g: 'cccc', v: 'chess' }, T0 + 1000),
        ];
        assert.deepEqual(JSON.parse(JSON.stringify(G.reduceGames(evs).order)),
                         ['bbbb', 'cccc', 'aaaa']);
    });
    test('games created in the same millisecond order by id, not by luck', () => {
        const evs = ['cccc', 'aaaa', 'bbbb'].map(g =>
            ev('u', { k: 'gm.new', g, v: 'chess' }, T0));
        assert.deepEqual(JSON.parse(JSON.stringify(G.reduceGames(evs).order)),
                         ['aaaa', 'bbbb', 'cccc']);
    });
    test('an unparseable timestamp degrades to 0 rather than NaN', () => {
        // NaN would poison every comparison and make ordering differ per
        // client depending on sort implementation.
        const g = G.reduceGames([
            { username: 'b', timestamp: 'not-a-date', p: { k: 'gm.new', g: 'g001', v: 'chess' } },
        ]).games.g001;
        assert.equal(g.createdAt, 0);
        assert.equal(g.lastAt, 0);
    });
    test('carriers for a game that was never opened do nothing', () => {
        for (const k of ['gm.join', 'gm.res', 'gm.draw', 'gm.claim']) {
            assert.equal(G.reduceGames([ev('b', { k, g: 'zzzz' })]).order.length, 0, k);
        }
    });
});

describe('determinism', () => {
    test('the same stream always folds to the same game', () => {
        const evs = [opened(), joined(),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4', 'e7e5', 'g1f3'])];
        const a = JSON.stringify(G.reduceGames(evs));
        const b = JSON.stringify(G.reduceGames(evs));
        assert.equal(a, b);
    });
    test('folding is not affected by a previous fold (no shared state)', () => {
        const evs = [opened(), joined(), ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4'])];
        const first = JSON.stringify(G.reduceGames(evs).games.g001);
        G.reduceGames([opened('zzzz', 'someone')]);
        assert.equal(JSON.stringify(G.reduceGames(evs).games.g001), first);
    });
    test('a late joiner folding only the tail reaches the same position', () => {
        // Whoever saw the whole stream and whoever adopted the checkpoint
        // must agree about the board, or the two players see different games.
        const players = ['boulder', 'kazimir'];
        const ucis = ['e2e4', 'e7e5', 'g1f3', 'b8c6'];
        const full = [opened(), joined(), ...moveEvents('g001', players, ucis)];
        const tail = moveEvents('g001', players, ucis).slice(-1).map(e => ({
            ...e, p: { ...e.p, v: 'chess' },
        }));
        assert.equal(G.reduceGames(full).games.g001.fen, G.reduceGames(tail).games.g001.fen);
    });
});

describe('connect 4 — the variant seam', () => {
    // The whole point: a second game with no change to the lifecycle. These
    // check that the shared machinery (seats, turns, endings, adoption) still
    // behaves, and that a hand-written position cannot smuggle in a disc.
    const EMPTY = '.'.repeat(42);
    const c4new = (gid = 'c001', by = 'boulder') =>
        ev(by, { k: 'gm.new', g: gid, v: 'connect4' });
    const c4join = (gid = 'c001', by = 'kazimir') =>
        ev(by, { k: 'gm.join', g: gid }, T0 + 1000);

    // Drop discs, computing each checkpoint honestly, alternating players.
    function drops(cols, players = ['boulder', 'kazimir'], gid = 'c001') {
        const heights = new Array(7).fill(0);
        const cells = new Array(42).fill('.');
        const out = [];
        cols.forEach((col, i) => {
            const who = i % 2 === 0 ? 'w' : 'b';
            cells[heights[col] * 7 + col] = who;
            heights[col]++;
            out.push(ev(players[i % 2],
                { k: 'gm.move', g: gid, v: 'connect4', n: i, m: String(col),
                  f: cells.join('') + ' ' + (who === 'w' ? 'b' : 'w') },
                T0 + 2000 + i));
        });
        return out;
    }
    const c4 = evs => G.reduceGames(evs).games.c001;

    test('a new game starts empty with white to move', () => {
        const g = c4([c4new(), c4join()]);
        assert.equal(g.variant, 'connect4');
        assert.equal(g.fen, EMPTY + ' w');
        assert.equal(G.toMove(g), 'boulder');
    });
    test('a disc falls to the lowest empty cell in its column', () => {
        const g = c4([c4new(), c4join(), ...drops([3])]);
        assert.equal(g.fen[3], 'w', 'bottom row, column 3');
        assert.equal(g.ply, 1);
        assert.equal(G.toMove(g), 'kazimir');
    });
    test('discs stack', () => {
        const g = c4([c4new(), c4join(), ...drops([3, 3, 3])]);
        assert.equal(g.fen.slice(0, 42).split('').filter(c => c !== '.').length, 3);
        assert.equal(g.fen[3], 'w');
        assert.equal(g.fen[3 + 7], 'b');
        assert.equal(g.fen[3 + 14], 'w');
    });
    test('four in a row wins, horizontally', () => {
        // w: 0,1,2,3 along the bottom; b answers in row 2 each time.
        const g = c4([c4new(), c4join(), ...drops([0, 0, 1, 1, 2, 2, 3])]);
        assert.equal(g.status, 'over');
        assert.equal(g.reason, 'four in a row');
        assert.equal(g.result, '1-0');
        assert.equal(g.winner, 'boulder');
    });
    test('four in a row wins, vertically', () => {
        const g = c4([c4new(), c4join(), ...drops([0, 1, 0, 1, 0, 1, 0])]);
        assert.equal(g.status, 'over');
        assert.equal(g.result, '1-0');
    });
    test('four in a row wins, diagonally', () => {
        // Builds a rising diagonal for white from (0,0) to (3,3).
        const g = c4([c4new(), c4join(),
            ...drops([0, 1, 1, 2, 2, 3, 2, 3, 3, 6, 3])]);
        assert.equal(g.status, 'over', G.reduceGames([c4new(), c4join(),
            ...drops([0, 1, 1, 2, 2, 3, 2, 3, 3, 6, 3])]).games.c001.fen);
        assert.equal(g.result, '1-0');
    });
    test('three in a row is not a win', () => {
        const g = c4([c4new(), c4join(), ...drops([0, 0, 1, 1, 2, 2])]);
        assert.equal(g.status, 'live');
    });
    test('a full column rejects another disc', () => {
        const g = c4([c4new(), c4join(),
            ...drops([0, 0, 0, 0, 0, 0]),                    // column 0 now full
            ev('boulder', { k: 'gm.move', g: 'c001', v: 'connect4', n: 6, m: '0' }, T0 + 9000)]);
        assert.equal(g.ply, 6);
    });
    test('a column outside the board is not a move', () => {
        for (const m of ['7', '-1', '', 'x', '10', 'e2e4', '3.5']) {
            const g = c4([c4new(), c4join(),
                ev('boulder', { k: 'gm.move', g: 'c001', v: 'connect4', n: 0, m }, T0 + 5000)]);
            assert.equal(g.ply, 0, JSON.stringify(m));
        }
    });
    test('the shared lifecycle still applies', () => {
        // Resignation, spectators and turn order are variant-agnostic.
        assert.equal(c4([c4new(), c4join(),
            ev('boulder', { k: 'gm.res', g: 'c001' }, T0 + 5000)]).winner, 'kazimir');
        assert.equal(c4([c4new(), c4join(),
            ev('sella', { k: 'gm.move', g: 'c001', v: 'connect4', n: 0, m: '3' },
               T0 + 5000)]).ply, 0, 'spectator cannot drop');
        assert.equal(c4([c4new(), c4join(),
            ev('kazimir', { k: 'gm.move', g: 'c001', v: 'connect4', n: 0, m: '3' },
               T0 + 5000)]).ply, 0, 'black cannot move first');
    });

    describe('adoption refuses a hand-written position', () => {
        // Same job as the chess engine's forged-en-passant gate: every client
        // would accept a fake board identically, so nothing would ever notice.
        const adopt = f => G.reduceGames([
            ev('mallory', { k: 'gm.move', g: 'c001', v: 'connect4', n: 3, m: '3', f }, T0),
        ]).order.length;

        test('a real position is accepted', () => {
            const cells = new Array(42).fill('.');
            cells[0] = 'w'; cells[1] = 'b'; cells[7] = 'w';   // 2 w, 1 b -> black to move
            assert.equal(adopt(cells.join('') + ' b'), 1);
        });
        test('a floating disc is refused', () => {
            const cells = new Array(42).fill('.');
            cells[7] = 'w';                                   // row 1 with row 0 empty
            assert.equal(adopt(cells.join('') + ' b'), 0);
        });
        test('impossible move counts are refused', () => {
            const cells = new Array(42).fill('.');
            cells[0] = 'w'; cells[1] = 'w'; cells[2] = 'w';   // 3 w, 0 b
            assert.equal(adopt(cells.join('') + ' b'), 0);
        });
        test('a turn that contradicts the counts is refused', () => {
            const cells = new Array(42).fill('.');
            cells[0] = 'w';                                   // 1 w, 0 b -> must be black
            assert.equal(adopt(cells.join('') + ' w'), 0);
        });
        test('malformed strings are refused', () => {
            for (const f of ['', '.'.repeat(42), '.'.repeat(41) + ' w', '.'.repeat(43) + ' w',
                             '.'.repeat(42) + ' x', 'x'.repeat(42) + ' w',
                             '.'.repeat(42) + ' w extra']) {
                assert.equal(adopt(f), 0, JSON.stringify(f.slice(0, 20)));
            }
        });
    });
});

describe('room vs player — a seat with no owner', () => {
    // The room's seat belongs to nobody, so no client can emit gm.move for
    // it. The FOLD commits the move once enough distinct people have voted,
    // which every client computes identically from the same stream.
    const roomGame = (extra = {}) =>
        ev('boulder', { k: 'gm.new', g: 'g001', v: 'chess', r: 1, ...extra });
    const vote = (by, uci, n = 0, at = T0 + 1000) =>
        ev(by, { k: 'gm.vote', g: 'g001', n, m: uci }, at);

    test('the room seat is filled from the start — nothing to join', () => {
        const g = one([roomGame()]);
        assert.equal(g.status, 'live');
        assert.equal(g.white, 'boulder');
        assert.equal(g.black, '', 'the room has no username');
        assert.equal(g.roomSeat, 'b');
        assert.equal(G.toMove(g), 'boulder', 'the human moves first');
    });
    test('nobody can join or claim the room seat', () => {
        assert.equal(one([roomGame(), joined('g001', 'kazimir')]).black, '');
        const played = [roomGame(), ...moveEvents('g001', ['boulder', ''], ['e2e4'])];
        const g = one([...played,
            ev('sella', { k: 'gm.claim', g: 'g001' }, T0 + 60000 + G.ABANDON_MS + 1)]);
        assert.equal(g.black, '', 'the room never goes idle, so there is nothing to take');
    });
    test('a vote below the threshold only tallies', () => {
        const g = one([roomGame(), ...moveEvents('g001', ['boulder', ''], ['e2e4']),
            vote('kazimir', 'e7e5', 1, T0 + 70000)]);
        assert.equal(g.ply, 1, 'not committed on one vote');
        assert.equal(g.votes.e7e5, 1);
        assert.equal(G.isRoomTurn(g), true);
        assert.equal(G.toMove(g), '', 'nobody personally owns this move');
    });
    test('the first move to reach the threshold is played by the fold', () => {
        const g = one([roomGame(), ...moveEvents('g001', ['boulder', ''], ['e2e4']),
            vote('kazimir', 'e7e5', 1, T0 + 70000),
            vote('sella', 'e7e5', 1, T0 + 71000)]);
        assert.equal(g.ply, 2, 'committed with no gm.move from anyone');
        assert.deepEqual(JSON.parse(JSON.stringify(g.moves)), ['e2e4', 'e7e5']);
        assert.equal(g.turn, 'w', 'back to the human');
        assert.deepEqual(JSON.parse(JSON.stringify(g.votes)), {}, 'ballot cleared');
    });
    test('a split vote carries nothing until one side reaches the threshold', () => {
        const base = [roomGame(), ...moveEvents('g001', ['boulder', ''], ['e2e4'])];
        let g = one([...base,
            vote('kazimir', 'e7e5', 1, T0 + 70000),
            vote('sella', 'c7c5', 1, T0 + 71000)]);
        assert.equal(g.ply, 1, 'one each, nothing carries');
        g = one([...base,
            vote('kazimir', 'e7e5', 1, T0 + 70000),
            vote('sella', 'c7c5', 1, T0 + 71000),
            vote('lain', 'c7c5', 1, T0 + 72000)]);
        assert.equal(g.ply, 2);
        assert.equal(g.moves[1], 'c7c5');
    });
    test('changing your mind moves your vote instead of adding one', () => {
        const g = one([roomGame(), ...moveEvents('g001', ['boulder', ''], ['e2e4']),
            vote('kazimir', 'e7e5', 1, T0 + 70000),
            vote('kazimir', 'c7c5', 1, T0 + 71000)]);
        assert.equal(g.ply, 1, 'one person cannot carry a vote alone');
        assert.equal(g.votes.e7e5, undefined);
        assert.equal(g.votes.c7c5, 1);
    });
    test('the human opponent does not get a vote in the room', () => {
        const g = one([roomGame(), ...moveEvents('g001', ['boulder', ''], ['e2e4']),
            vote('boulder', 'e7e5', 1, T0 + 70000),
            vote('kazimir', 'e7e5', 1, T0 + 71000)]);
        assert.equal(g.ply, 1, 'only kazimir counted');
        assert.equal(g.votes.e7e5, 1);
    });
    test('illegal and stale ballots are dropped', () => {
        const base = [roomGame(), ...moveEvents('g001', ['boulder', ''], ['e2e4'])];
        assert.equal(one([...base,
            vote('kazimir', 'e2e4', 1, T0 + 70000),
            vote('sella', 'e2e4', 1, T0 + 71000)]).ply, 1, 'not a legal black move');
        assert.equal(one([...base,
            vote('kazimir', 'e7e5', 0, T0 + 70000),
            vote('sella', 'e7e5', 0, T0 + 71000)]).ply, 1, 'ballot for a finished ply');
    });
    test('nobody may vote while the human is on move', () => {
        const g = one([roomGame(),
            vote('kazimir', 'e2e4', 0, T0 + 1000),
            vote('sella', 'e2e4', 0, T0 + 2000)]);
        assert.equal(g.ply, 0);
    });
    test('the threshold is configurable and clamped', () => {
        const g = one([roomGame({ kv: 1 }), ...moveEvents('g001', ['boulder', ''], ['e2e4']),
            vote('kazimir', 'e7e5', 1, T0 + 70000)]);
        assert.equal(g.ply, 2, 'kv:1 commits on a single vote');
        assert.equal(one([roomGame({ kv: 999 })]).voteK, 9);
        assert.equal(one([roomGame({ kv: 0 })]).voteK, 1);
        assert.equal(one([roomGame({ kv: 'lots' })]).voteK, 2, 'nonsense falls back');
    });
    test('a vote can deliver checkmate', () => {
        // Fool's mate with the room playing black.
        const evs = [roomGame(),
            ...moveEvents('g001', ['boulder', ''], ['f2f3'])];
        const g = one([...evs,
            vote('kazimir', 'e7e5', 1, T0 + 70000), vote('sella', 'e7e5', 1, T0 + 71000),
            ev('boulder', { k: 'gm.move', g: 'g001', n: 2, m: 'g2g4' }, T0 + 72000),
            vote('kazimir', 'd8h4', 3, T0 + 73000), vote('sella', 'd8h4', 3, T0 + 74000)]);
        assert.equal(g.status, 'over');
        assert.equal(g.reason, 'checkmate');
        assert.equal(g.result, '0-1');
        assert.equal(g.winner, '', 'the room won, and the room is not a username');
        assert.equal(g.winnerColor, 'b');
    });
    test('a room game is not rated — one side is not a person', () => {
        const g = one([roomGame(), ev('boulder', { k: 'gm.res', g: 'g001' }, T0 + 5000)]);
        assert.equal(g.status, 'over');
        assert.deepEqual(JSON.parse(JSON.stringify(G.ratings(G.reduceGames([
            roomGame(), ev('boulder', { k: 'gm.res', g: 'g001' }, T0 + 5000),
        ])))), []);
    });
    test('votes never touch an ordinary two-player game', () => {
        const g = one([opened(), joined(),
            vote('sella', 'e2e4', 0, T0 + 5000), vote('lain', 'e2e4', 0, T0 + 6000)]);
        assert.equal(g.ply, 0);
        assert.equal(g.roomSeat, '');
    });
});

describe('ratings — a ladder with no server', () => {
    // Elo is order-dependent, so the whole risk here is two clients folding
    // the same results into different numbers.
    const DAY = 24 * 60 * 60 * 1000;

    // A finished game between two named players, ending in `result`.
    function finished(id, white, black, result, at) {
        const evs = [
            ev(white, { k: 'gm.new', g: id, v: 'chess' }, at),
            ev(black, { k: 'gm.join', g: id }, at + 1),
        ];
        // Resignation is the cheapest way to land an exact result.
        if (result === '1-0') evs.push(ev(black, { k: 'gm.res', g: id }, at + 2));
        else if (result === '0-1') evs.push(ev(white, { k: 'gm.res', g: id }, at + 2));
        else {
            evs.push(ev(white, { k: 'gm.draw', g: id }, at + 2));
            evs.push(ev(black, { k: 'gm.draw', g: id }, at + 3));
        }
        return evs;
    }
    const rate = evs => G.ratings(G.reduceGames(evs));

    test('an unplayed room has an empty ladder', () => {
        assert.deepEqual(JSON.parse(JSON.stringify(rate([opened()]))), []);
    });
    test('a win moves both players symmetrically off 1200', () => {
        const table = rate(finished('aaaa', 'boulder', 'kazimir', '1-0', T0));
        const w = table.find(r => r.name === 'boulder');
        const b = table.find(r => r.name === 'kazimir');
        assert.equal(w.rating, G.ELO_START + 16, 'even match, K=32, full point');
        assert.equal(b.rating, G.ELO_START - 16);
        assert.equal(w.wins, 1); assert.equal(b.losses, 1);
        assert.equal(table[0].name, 'boulder', 'sorted by rating');
    });
    test('a draw between equals moves nobody', () => {
        const table = rate(finished('aaaa', 'boulder', 'kazimir', '1/2-1/2', T0));
        assert.equal(table[0].rating, G.ELO_START);
        assert.equal(table[1].rating, G.ELO_START);
        assert.equal(table[0].draws, 1);
    });
    test('near-simultaneous games order identically despite clock skew', () => {
        // Finish times come from each user's own slskd, so they differ by
        // latency. Comparing at millisecond resolution would let two clients
        // rate the same two games in a different order and reach different
        // numbers. Rounding to whole seconds plus an id tiebreak fixes it.
        const a = finished('aaaa', 'boulder', 'kazimir', '1-0', T0);
        const b = finished('bbbb', 'kazimir', 'sella', '1-0', T0 + 300);
        const base = JSON.stringify(rate([...a, ...b]));
        // Same games, the other client's copy jittered by a few hundred ms.
        const jitter = ms => finished('aaaa', 'boulder', 'kazimir', '1-0', T0 + ms);
        for (const ms of [-400, -120, 120, 400]) {
            assert.equal(JSON.stringify(rate([...jitter(ms), ...b])), base, `skew ${ms}ms`);
        }
    });
    test('the id breaks a tie so the order is total', () => {
        const a = finished('aaaa', 'boulder', 'kazimir', '1-0', T0);
        const b = finished('bbbb', 'kazimir', 'sella', '1-0', T0);
        // Same second: whichever order the events arrive in, the ladder is
        // computed in id order and so comes out the same.
        assert.equal(JSON.stringify(rate([...a, ...b])), JSON.stringify(rate([...b, ...a])));
    });
    test('results compound in order rather than being averaged', () => {
        const evs = [
            ...finished('aaaa', 'boulder', 'kazimir', '1-0', T0),
            ...finished('bbbb', 'boulder', 'kazimir', '1-0', T0 + 60000),
            ...finished('cccc', 'boulder', 'kazimir', '1-0', T0 + 120000),
        ];
        const w = rate(evs).find(r => r.name === 'boulder');
        assert.equal(w.games, 3);
        assert.equal(w.wins, 3);
        // Each win is worth less than the last as the gap opens up.
        assert.ok(w.rating > G.ELO_START + 16 * 2 && w.rating < G.ELO_START + 16 * 3,
                  `diminishing returns, got ${w.rating}`);
    });
    test('unfinished, abandoned and one-sided games are not rated', () => {
        assert.deepEqual(JSON.parse(JSON.stringify(rate([opened(), joined()]))), [],
                         'a live game is not a result');
        // A game nobody joined has no opponent to rate against.
        assert.deepEqual(JSON.parse(JSON.stringify(rate([
            opened('aaaa', 'boulder'),
            ev('boulder', { k: 'gm.res', g: 'aaaa' }, T0 + 5),
        ]))), []);
    });
    test('a game adopted mid-stream is not rated', () => {
        // Its seats were deduced from whoever moved, not observed from
        // gm.new, so the ladder stays out of it.
        const midFen = 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3';
        const evs = [
            ev('kazimir', { k: 'gm.move', g: 'aaaa', v: 'chess', n: 7, m: 'e7e5', f: midFen }, T0),
            ev('boulder', { k: 'gm.move', g: 'aaaa', n: 8, m: 'g1f3' }, T0 + 1000),
            ev('boulder', { k: 'gm.res', g: 'aaaa' }, T0 + 2000),
        ];
        const g = G.reduceGames(evs).games.aaaa;
        assert.equal(g.status, 'over', 'the game really did finish');
        assert.deepEqual(JSON.parse(JSON.stringify(rate(evs))), [], 'but it is unrated');
    });
    test('a seat claimed after abandonment rates its final occupant', () => {
        const base = [opened(), joined(),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4'])];
        const lastAt = T0 + 60000;
        const evs = [...base,
            ev('sella', { k: 'gm.claim', g: 'g001' }, lastAt + DAY + 1000),
            ev('sella', { k: 'gm.res', g: 'g001' }, lastAt + DAY + 2000)];
        const table = rate(evs);
        assert.ok(table.find(r => r.name === 'sella'), 'the player who actually finished it');
        assert.ok(!table.find(r => r.name === 'kazimir'), 'not the one who walked away');
    });
    test('folding twice gives the same table', () => {
        const evs = [
            ...finished('aaaa', 'boulder', 'kazimir', '1-0', T0),
            ...finished('bbbb', 'sella', 'boulder', '0-1', T0 + 60000),
            ...finished('cccc', 'kazimir', 'sella', '1/2-1/2', T0 + 120000),
        ];
        assert.equal(JSON.stringify(rate(evs)), JSON.stringify(rate(evs)));
    });
});

describe('toMove — what drives the "your move" badge', () => {
    test('follows the position, not ply parity', () => {
        // An adopted game takes its ply from the wire. A wrong or hostile
        // `n` whose parity disagrees with the FEN must not badge the player
        // who just moved — and ply parity is a chess assumption that other
        // variants will not share.
        const fen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1';
        const g = G.reduceGames([ev('boulder',
            { k: 'gm.move', g: 'g001', v: 'chess', n: 7, m: 'e2e4', f: fen }, T0)]).games.g001;
        assert.equal(g.turn, 'b', 'the position says black');
        assert.equal(g.ply % 2, 0, 'while the wire ply parity says white');
        assert.equal(G.toMove(g), g.black, 'the position wins');
        assert.notEqual(G.toMove(g), 'boulder', 'never the player who just moved');
    });
    test('a claimed seat does not inherit a draw offer made to whoever left', () => {
        const DAY = G.ABANDON_MS;
        const base = [opened(), joined(), ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4'])];
        const lastAt = T0 + 60000;
        const g = one([...base,
            ev('boulder', { k: 'gm.draw', g: 'g001' }, lastAt + 1),
            ev('sella', { k: 'gm.claim', g: 'g001' }, lastAt + DAY + 2000),
            ev('sella', { k: 'gm.draw', g: 'g001' }, lastAt + DAY + 3000)]);
        assert.equal(g.status, 'live', 'sella cannot accept an offer she was never made');
        assert.equal(g.drawOffer, 'sella', 'it counts as her own fresh offer');
    });
    test('alternates from white and is empty when not live', () => {
        assert.equal(G.toMove(one([opened()])), '', 'nobody moves in an open game');
        assert.equal(G.toMove(one([opened(), joined()])), 'boulder');
        assert.equal(G.toMove(one([opened(), joined(),
            ...moveEvents('g001', ['boulder', 'kazimir'], ['e2e4'])])), 'kazimir');
        assert.equal(G.toMove(one([opened(), joined(),
            ev('boulder', { k: 'gm.res', g: 'g001' }, T0 + 1)])), '');
        assert.equal(G.toMove(null), '');
    });
});
