// Chess engine contract (webui/static/chess-engine.js).
// Run via: node --test tests/static/test_chess_engine.mjs
// (pytest wrapper: tests/test_chess_engine_js.py)
//
// This engine is the arbiter for games played over a Soulseek room with no
// server. Every client runs it against the same move stream, so a legality
// bug does not produce an error — it produces two players looking at
// different boards. That is why the core of this suite is PERFT against the
// standard positions rather than a handful of example games: perft counts
// every leaf of the move tree, so a single missing or spurious move at any
// depth changes the number. Example games would pass with a broken engine.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, '../../webui/static/chess-engine.js'), 'utf-8');
const ctx = { window: {} };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const E = ctx.window.ChessEngine;

// VM-created arrays are cross-realm (different prototype chain), so a plain
// deepStrictEqual against a literal fails on the prototype alone. Compare
// shapes via JSON round-trip — same pattern as test_chat_protocol.mjs.
function shapeEqual(actual, expected, msg) {
    assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected, msg);
}

function perft(pos, depth) {
    if (depth === 0) return 1;
    const moves = E.legalMoves(pos);
    if (depth === 1) return moves.length;
    let nodes = 0;
    for (const m of moves) nodes += perft(E.makeMove(pos, m), depth - 1);
    return nodes;
}

// The canonical perft suite (chessprogramming.org). These numbers are not
// ours to negotiate — if one is off by a single node, move generation is
// wrong somewhere and the arcade is unsafe to ship.
const PERFT = [
    {
        name: 'start position',
        fen: E.START_FEN,
        counts: [20, 400, 8902, 197281],
    },
    {
        name: 'kiwipete (castling, pins, ep)',
        fen: 'r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1',
        counts: [48, 2039, 97862],
    },
    {
        name: 'position 3 (promotion race, ep edge cases)',
        fen: '8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1',
        counts: [14, 191, 2812, 43238],
    },
    {
        name: 'position 4 (under-promotion, discovered check)',
        fen: 'r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1',
        counts: [6, 264, 9467],
    },
    {
        name: 'position 5 (no ep, tight king)',
        fen: 'rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8',
        counts: [44, 1486, 62379],
    },
    {
        name: 'position 6 (quiet middlegame)',
        fen: 'r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10',
        counts: [46, 2079, 89890],
    },
];

// One tier deeper, ~15s total, so it is opt-in rather than paid on every CI
// run: CHESS_PERFT_DEEP=1 node --test tests/static/test_chess_engine.mjs
// These all passed on the engine as committed — 4,865,609 nodes on the start
// position and 4,085,603 on kiwipete is a real proof of move generation, not
// a smoke test. Re-run it after ANY change to generation or makeMove.
const PERFT_DEEP = [
    ['start position', E.START_FEN, 5, 4865609],
    ['kiwipete', 'r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1', 4, 4085603],
    ['position 3', '8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1', 5, 674624],
    ['position 4', 'r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1', 4, 422333],
    ['position 5', 'rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8', 4, 2103487],
    ['position 6', 'r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10', 4, 3894594],
];

describe('perft — move generation is exactly correct', () => {
    for (const p of PERFT) {
        test(p.name, () => {
            const pos = E.fromFEN(p.fen);
            assert.ok(pos, 'FEN parsed');
            p.counts.forEach((expected, i) => {
                assert.equal(perft(pos, i + 1), expected, `depth ${i + 1}`);
            });
        });
    }

    for (const [name, fen, depth, expected] of PERFT_DEEP) {
        test(`${name} — depth ${depth} (deep)`, { skip: !process.env.CHESS_PERFT_DEEP }, () => {
            assert.equal(perft(E.fromFEN(fen), depth), expected);
        });
    }
});

describe('FEN round-trip', () => {
    test('start position survives a round trip', () => {
        assert.equal(E.toFEN(E.newGame()), E.START_FEN);
    });
    test('every perft position round-trips byte-for-byte', () => {
        for (const p of PERFT) assert.equal(E.toFEN(E.fromFEN(p.fen)), p.fen, p.name);
    });
    test('malformed FEN is rejected, not guessed at', () => {
        // A parser that "recovers" from junk would let one client build a
        // board another client refuses — silence is the dangerous outcome.
        assert.equal(E.fromFEN(''), null);
        assert.equal(E.fromFEN('not a fen'), null);
        assert.equal(E.fromFEN('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP w KQkq - 0 1'), null); // 7 ranks
        assert.equal(E.fromFEN('rnbqkbnr/ppppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'), null); // 9 files
        assert.equal(E.fromFEN('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR x KQkq - 0 1'), null); // bad turn
        assert.equal(E.fromFEN('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w XYZ - 0 1'), null); // bad rights
        assert.equal(E.fromFEN('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq z9 0 1'), null); // bad ep
        assert.equal(E.fromFEN('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR'), null); // truncated
    });
});

describe('isLegalPosition — the wire gate', () => {
    // Games carry a FEN checkpoint so they survive the room's rolling
    // archive, and that FEN comes from a machine we do not control. These
    // are not hypothetical: every rejection below is a position fromFEN
    // accepts happily, and the ep one lets a pawn move sideways onto an
    // empty square on EVERY client at once, so no desync ever reveals it.
    test('real positions pass', () => {
        for (const p of PERFT) assert.ok(E.isLegalPosition(E.fromFEN(p.fen)), p.name);
        assert.ok(E.isLegalPosition(E.newGame()));
    });
    test('a forged en passant square is rejected', () => {
        // d3 claimed with no white pawn on d4 to have pushed there.
        const forged = '4k3/8/8/8/8/8/4P3/4K3 w - d3 0 1';
        assert.ok(E.fromFEN(forged), 'well-formed text, so fromFEN takes it');
        assert.equal(E.isLegalPosition(E.fromFEN(forged)), false);
        assert.equal(E.fromWireFEN(forged), null, 'and the wire gate refuses it');
    });
    test('a genuine en passant square is kept', () => {
        const real = '4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1';
        assert.ok(E.fromWireFEN(real));
    });
    test('the ep square must sit on the right rank for the side to move', () => {
        assert.equal(E.fromWireFEN('4k3/8/8/3pP3/8/8/8/4K3 w - d3 0 1'), null);
    });
    test('missing, duplicated or extra kings are rejected', () => {
        assert.equal(E.fromWireFEN('4K3/8/8/8/8/8/8/8 w - - 0 1'), null, 'no black king');
        assert.equal(E.fromWireFEN('4k3/8/8/8/8/8/8/8 w - - 0 1'), null, 'no white king');
        assert.equal(E.fromWireFEN('4k1k1/8/8/8/8/8/8/4K3 w - - 0 1'), null, 'two black kings');
    });
    test('a pawn on the first or last rank is rejected', () => {
        assert.equal(E.fromWireFEN('4k3/8/8/8/8/8/8/4Kp2 w - - 0 1'), null);
        assert.equal(E.fromWireFEN('4k1P1/8/8/8/8/8/8/4K3 b - - 0 1'), null);
    });
    test('leaving the side that just moved in check is rejected', () => {
        // White to move with black already in check from the rook down the
        // open e-file: black would have had to hand over the move while
        // leaving its own king hanging.
        assert.equal(E.fromWireFEN('4k3/8/8/8/8/8/8/K3R3 w - - 0 1'), null);
        // Same board with black to move is the legal version of it.
        assert.ok(E.fromWireFEN('4k3/8/8/8/8/8/8/K3R3 b - - 0 1'));
    });
    test('impossible piece counts are rejected', () => {
        assert.equal(E.fromWireFEN('4k3/pppppppp/pppppppp/8/8/8/8/4K3 w - - 0 1'), null,
                     'sixteen black pawns');
    });
    test('garbage never reaches the board', () => {
        for (const bad of ['', 'nonsense', null, undefined, 42, {}]) {
            assert.equal(E.fromWireFEN(bad), null, JSON.stringify(bad));
        }
    });
});

describe('castling', () => {
    test('rights are lost when the rook is CAPTURED, not just moved', () => {
        // The classic engine bug. Black rook on a8 is taken by the white
        // rook on a1; black must lose the queenside right even though no
        // black piece moved.
        const pos = E.fromFEN('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1');
        const after = E.makeMove(pos, E.uciToMove(pos, 'a1a8'));
        assert.equal(after.castling, 'Kk', 'Q lost (rook left a1), q lost (rook died on a8)');
    });
    test('rights are lost when the king moves', () => {
        const pos = E.fromFEN('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1');
        const after = E.makeMove(pos, E.uciToMove(pos, 'e1e2'));
        assert.equal(after.castling, 'kq');
    });
    test('cannot castle out of, through, or into check', () => {
        // Rook on e8 attacks e1: castling out of check.
        let pos = E.fromFEN('4r3/8/8/8/8/8/8/R3K2R w KQ - 0 1');
        assert.ok(!E.uciToMove(pos, 'e1g1'), 'out of check');
        // Rook on f8 attacks f1, the square the king crosses.
        pos = E.fromFEN('5r2/8/8/8/8/8/8/R3K2R w KQ - 0 1');
        assert.ok(!E.uciToMove(pos, 'e1g1'), 'through check');
        // Rook on g8 attacks g1, the square the king lands on.
        pos = E.fromFEN('6r1/8/8/8/8/8/8/R3K2R w KQ - 0 1');
        assert.ok(!E.uciToMove(pos, 'e1g1'), 'into check');
    });
    test('queenside is legal with b1 attacked — the king never crosses it', () => {
        // Rook on b8 attacks b1. The king goes e1->d1->c1, so this is legal;
        // engines that test all three empty squares for attack get it wrong.
        const pos = E.fromFEN('1r6/8/8/8/8/8/8/R3K2R w KQ - 0 1');
        assert.ok(E.uciToMove(pos, 'e1c1'), 'queenside castle still legal');
    });
    test('queenside is blocked when b1 is OCCUPIED', () => {
        const pos = E.fromFEN('8/8/8/8/8/8/8/RN2K2R w KQ - 0 1');
        assert.ok(!E.uciToMove(pos, 'e1c1'));
    });
    test('the rook actually lands beside the king', () => {
        let pos = E.fromFEN('8/8/8/8/8/8/8/R3K2R w KQ - 0 1');
        let after = E.makeMove(pos, E.uciToMove(pos, 'e1g1'));
        // a1 rook is untouched; h1 rook swings to f1 and the king to g1.
        assert.equal(E.toFEN(after).split(' ')[0], '8/8/8/8/8/8/8/R4RK1');
        after = E.makeMove(pos, E.uciToMove(pos, 'e1c1'));
        assert.equal(E.toFEN(after).split(' ')[0], '8/8/8/8/8/8/8/2KR3R');
    });
});

describe('en passant', () => {
    test('the ep square appears only when a pawn can actually take', () => {
        // Black pawn on d4 sits beside the pushed e-pawn: ep square is set.
        let pos = E.fromFEN('4k3/8/8/8/3p4/8/4P3/4K3 w - - 0 1');
        let after = E.makeMove(pos, E.uciToMove(pos, 'e2e4'));
        assert.equal(E.toFEN(after).split(' ')[3], 'e3');

        // No black pawn adjacent: no ep square, so two otherwise identical
        // positions do not compare unequal and suppress a threefold claim.
        pos = E.fromFEN('4k3/8/8/8/8/8/4P3/4K3 w - - 0 1');
        after = E.makeMove(pos, E.uciToMove(pos, 'e2e4'));
        assert.equal(E.toFEN(after).split(' ')[3], '-');
    });
    test('capturing en passant removes the pawn beside the target square', () => {
        const pos = E.fromFEN('4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1');
        const after = E.makeMove(pos, E.uciToMove(pos, 'e5d6'));
        assert.equal(E.toFEN(after).split(' ')[0], '4k3/8/3P4/8/8/8/8/4K3');
    });
    test('an ep capture that exposes the king is illegal', () => {
        // Both pawns leave the fifth rank at once, opening the white rook's
        // line onto the black king. Only a make-then-test can catch this.
        const pos = E.fromFEN('8/8/8/K1pP3r/8/8/8/7k w - c6 0 1');
        assert.ok(!E.uciToMove(pos, 'd5c6'), 'ep capture is pinned');
    });
    test('a pawn cannot wrap around the board edge to capture', () => {
        // White pawn on a5; h4 is "a5-17" in 0x88 terms but a different file.
        const pos = E.fromFEN('4k3/8/8/P7/7p/8/8/4K3 w - - 0 1');
        const ucis = E.legalMoves(pos).map(E.moveToUci);
        assert.ok(!ucis.includes('a5h4'));
    });
});

describe('promotion', () => {
    test('all four promotions are offered, not just the queen', () => {
        const pos = E.fromFEN('8/4P3/8/8/8/8/8/4K1k1 w - - 0 1');
        const promos = E.legalMoves(pos).filter(m => m.from === E.fromAlg('e7')).map(E.moveToUci);
        shapeEqual(promos.sort(), ['e7e8b', 'e7e8n', 'e7e8q', 'e7e8r']);
    });
    test('under-promotion to knight is playable and lands a knight', () => {
        const pos = E.fromFEN('8/4P3/8/8/8/8/8/4K1k1 w - - 0 1');
        const after = E.makeMove(pos, E.uciToMove(pos, 'e7e8n'));
        assert.equal(after.board[E.fromAlg('e8')], 'N');
    });
    test('black promotes to a lowercase piece', () => {
        const pos = E.fromFEN('4k1K1/8/8/8/8/8/4p3/8 b - - 0 1');
        const after = E.makeMove(pos, E.uciToMove(pos, 'e2e1q'));
        assert.equal(after.board[E.fromAlg('e1')], 'q');
    });
});

describe('terminal states', () => {
    test('fool\'s mate is checkmate for black', () => {
        const pos = E.fromFEN('rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3');
        const s = E.status(pos, []);
        assert.equal(s.over, true);
        assert.equal(s.reason, 'checkmate');
        assert.equal(s.result, '0-1');
        assert.equal(s.winner, 'b');
    });
    test('stalemate is a draw, not a loss', () => {
        const pos = E.fromFEN('7k/5Q2/6K1/8/8/8/8/8 b - - 0 1');
        const s = E.status(pos, []);
        assert.equal(s.over, true);
        assert.equal(s.reason, 'stalemate');
        assert.equal(s.result, '1/2-1/2');
    });
    test('fifty-move rule fires at 100 half-moves', () => {
        const live = E.fromFEN('8/8/4k3/8/8/4K3/8/6R1 w - - 99 80');
        assert.equal(E.status(live, []).over, false);
        const dead = E.fromFEN('8/8/4k3/8/8/4K3/8/6R1 w - - 100 80');
        assert.equal(E.status(dead, []).reason, 'fifty');
    });
    test('threefold repetition needs three occurrences, not two', () => {
        const pos = E.fromFEN('8/8/4k3/8/8/4K3/8/6R1 w - - 10 40');
        const key = E.positionKey(pos);
        assert.equal(E.status(pos, [key, key]).over, false, 'twice is not enough');
        assert.equal(E.status(pos, [key, key, key]).reason, 'repetition');
    });
    test('insufficient material covers the drawn endings and no others', () => {
        const draw = fen => E.status(E.fromFEN(fen), []).reason === 'insufficient';
        assert.ok(draw('8/8/4k3/8/8/4K3/8/8 w - - 0 1'), 'K vs K');
        assert.ok(draw('8/8/4k3/8/8/4K3/8/6B1 w - - 0 1'), 'K+B vs K');
        assert.ok(draw('8/8/4k3/8/8/4K3/8/6N1 w - - 0 1'), 'K+N vs K');
        // Same-coloured bishops draw; opposite-coloured do not. Square colour
        // is (file + rank) parity with a1 = (0,0) = dark, so c1 and g5 are
        // both dark while f5 is light.
        assert.ok(draw('4k3/8/8/6b1/8/8/8/2B1K3 w - - 0 1'), 'K+B vs K+B same colour');
        assert.ok(!draw('4k3/8/8/5b2/8/8/8/2B1K3 w - - 0 1'), 'K+B vs K+B opposite colour');
        assert.ok(!draw('8/8/4k3/8/8/4K3/8/6R1 w - - 0 1'), 'a rook can mate');
        assert.ok(!draw('8/8/4k3/8/8/4K3/4P3/8 w - - 0 1'), 'a pawn can promote');
    });
    test('a checked-but-alive position is not over', () => {
        // Rook on e1 checks down the open e-file; the king still has d7/d8/f7/f8.
        const pos = E.fromFEN('4k3/8/8/8/8/8/8/K3R3 b - - 0 1');
        const s = E.status(pos, []);
        assert.equal(s.over, false);
        assert.equal(s.check, true);
        assert.equal(s.reason, 'check');
    });
});

describe('UCI wire format — hostile input', () => {
    // Anything on the wire came from another machine we do not control, so
    // uciToMove is a validator first and a parser second: it resolves only
    // against legal moves and returns null for everything else.
    const pos = E.newGame();
    test('legal moves resolve', () => {
        assert.equal(E.moveToUci(E.uciToMove(pos, 'e2e4')), 'e2e4');
        assert.equal(E.uciToMove(pos, 'e2e4').flags, 'double');
    });
    test('illegal, malformed and hostile input all return null', () => {
        const bad = ['', 'e2', 'e2e', 'e2e4e5', 'z9z9', 'e2e5', 'e7e5', 'e2e4q',
                     '1234', 'O-O', null, undefined, 42, {}, [], 'e2e4\n'];
        for (const b of bad) assert.equal(E.uciToMove(pos, b), null, JSON.stringify(b));
    });
    test('promotion suffix must name a real piece', () => {
        const p = E.fromFEN('8/4P3/8/8/8/8/8/4K1k1 w - - 0 1');
        assert.ok(E.uciToMove(p, 'e7e8q'));
        assert.equal(E.uciToMove(p, 'e7e8k'), null, 'cannot promote to king');
        assert.equal(E.uciToMove(p, 'e7e8p'), null, 'cannot promote to pawn');
    });
    test('a move that is legal for the other side is rejected on your turn', () => {
        assert.equal(E.uciToMove(pos, 'e7e5'), null);
    });
});

describe('immutability', () => {
    test('makeMove never touches the position it was given', () => {
        // A fold keeps every intermediate position; if makeMove mutated,
        // replaying history would corrupt it silently.
        const pos = E.newGame();
        const before = E.toFEN(pos);
        E.makeMove(pos, E.uciToMove(pos, 'e2e4'));
        assert.equal(E.toFEN(pos), before);
    });
    test('legalMoves does not disturb the position either', () => {
        const pos = E.fromFEN('r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1');
        const before = E.toFEN(pos);
        E.legalMoves(pos);
        E.status(pos, []);
        assert.equal(E.toFEN(pos), before);
    });
});

describe('SAN', () => {
    const san = (fen, uci) => {
        const pos = E.fromFEN(fen);
        return E.toSAN(pos, E.uciToMove(pos, uci));
    };
    test('plain moves and captures', () => {
        assert.equal(san(E.START_FEN, 'e2e4'), 'e4');
        assert.equal(san(E.START_FEN, 'g1f3'), 'Nf3');
        assert.equal(san('4k3/8/8/3p4/4N3/8/8/4K3 w - - 0 1', 'e4d6'), 'Nd6+');
    });
    test('pawn captures carry the origin file', () => {
        assert.equal(san('4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1', 'e4d5'), 'exd5');
    });
    test('castling', () => {
        assert.equal(san('8/8/8/8/8/8/8/R3K2R w KQ - 0 1', 'e1g1'), 'O-O');
        assert.equal(san('8/8/8/8/8/8/8/R3K2R w KQ - 0 1', 'e1c1'), 'O-O-O');
    });
    test('promotion and mate markers', () => {
        assert.equal(san('8/4P3/8/8/8/8/8/4K1k1 w - - 0 1', 'e7e8n'), 'e8=N');
        // Back-rank mate: rook drops to a8 with the black king boxed in.
        assert.equal(san('6k1/5ppp/8/8/8/8/8/R3K3 w Q - 0 1', 'a1a8'), 'Ra8#');
    });
    test('disambiguation by file, then rank, then both', () => {
        // Two knights on d2 and f2 both reach e4 — file distinguishes them.
        assert.equal(san('4k3/8/8/8/8/8/3N1N2/4K3 w - - 0 1', 'd2e4'), 'Nde4');
        // Two rooks on the same file (a1, a3) reaching a2 — rank does it.
        assert.equal(san('4k3/8/8/8/8/R7/8/R3K3 w - - 0 1', 'a1a2'), 'R1a2');
        // Three queens covering d5 from a5, d8 and h5 — a5 shares its rank
        // with h5 and its file with nothing, so the file alone suffices.
        assert.equal(san('3q4/8/8/q6q/8/8/8/4K1k1 b - - 0 1', 'a5d5'), 'Qad5');
    });
});

describe('PGN', () => {
    test('exports a real game with the seven-tag roster', () => {
        const moves = ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'];
        const pgn = E.toPGN(moves, {
            White: 'boulderbadgedad', Black: 'kazimir', Result: '*', Date: '2026.07.25',
        });
        assert.ok(pgn.includes('[White "boulderbadgedad"]'));
        assert.ok(pgn.includes('[Black "kazimir"]'));
        assert.ok(pgn.includes('[Event "SoulSync Arcade"]'));
        assert.ok(pgn.includes('1. e4 e5 2. Nf3 Nc6 3. Bb5'), pgn);
    });
    test('a quoted name cannot break out of the tag', () => {
        const pgn = E.toPGN([], { White: 'he said "hi"' });
        assert.ok(pgn.includes('[White "he said \'hi\'"]'), pgn);
    });
    test('stops cleanly at the first illegal move instead of throwing', () => {
        const pgn = E.toPGN(['e2e4', 'e7e5', 'e2e4'], {});
        assert.ok(pgn.includes('1. e4 e5'));
        assert.ok(!pgn.includes('2.'), pgn);
    });
    test('a game resumed on black\'s move is numbered "12..." not "12."', () => {
        const pgn = E.toPGN(['e8f8'], {}, '4k3/8/8/8/8/8/8/4K2R b - - 0 12');
        assert.ok(pgn.includes('12... Kf8'), pgn);
    });
    test('records the start position when it is not the standard one', () => {
        const fen = '4k3/8/8/8/8/8/4P3/4K3 w - - 0 1';
        const pgn = E.toPGN(['e2e4'], {}, fen);
        assert.ok(pgn.includes('[SetUp "1"]'));
        assert.ok(pgn.includes('[FEN "' + fen + '"]'));
    });
});
