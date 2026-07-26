// SoulSync chess engine — pure, deterministic, zero DOM, zero fetch.
//
// There is no game server. Every client folds the same room message stream
// into the same board, so THIS FILE is the shared arbiter: an illegal move
// has to be rejected identically on every machine or two players silently
// diverge and the game forks with nobody noticing.
//
// That is why this implements the whole ruleset rather than a playable
// subset — castling rights lost on rook CAPTURE as well as rook move, en
// passant, under-promotion, threefold repetition, the fifty-move rule,
// insufficient material — and why the suite runs perft against the standard
// positions instead of a few example games. Perft is the only test that
// actually proves move generation; example games prove nothing.
//
// Board is 0x88: 128 squares, a1 = 0, h1 = 7, a8 = 112, h8 = 119. A square
// is off-board iff (sq & 0x88) is non-zero, which makes every slide and
// knight hop a single bit test instead of file/rank arithmetic.
//
// Positions are IMMUTABLE — makeMove returns a new one — so a fold can keep
// every intermediate state without defensive copying, and replaying a move
// list can never mutate a caller's board out from under it.
//
// Unit-tested in isolation: tests/static/test_chess_engine.mjs

(function () {
    'use strict';

    // ── Board geometry ──────────────────────────────────────────────────

    var OFF = 0x88;
    function onBoard(sq) { return (sq & OFF) === 0; }
    function fileOf(sq) { return sq & 7; }
    function rankOf(sq) { return sq >> 4; }
    function sqOf(file, rank) { return rank * 16 + file; }

    var FILES = 'abcdefgh';

    function toAlg(sq) { return FILES[fileOf(sq)] + (rankOf(sq) + 1); }

    function fromAlg(s) {
        if (typeof s !== 'string' || s.length < 2) return -1;
        var f = FILES.indexOf(s[0]);
        var r = parseInt(s[1], 10) - 1;
        if (f < 0 || !(r >= 0 && r <= 7)) return -1;
        return sqOf(f, r);
    }

    // Ray offsets. Knights and kings are single hops; the rest slide.
    var KNIGHT_HOPS = [33, 31, 18, 14, -33, -31, -18, -14];
    var BISHOP_RAYS = [17, 15, -17, -15];
    var ROOK_RAYS = [16, 1, -16, -1];
    var QUEEN_RAYS = ROOK_RAYS.concat(BISHOP_RAYS);
    var KING_HOPS = QUEEN_RAYS;

    // ── Pieces ──────────────────────────────────────────────────────────
    // Uppercase = white, lowercase = black, '' = empty. Char-per-square is
    // slower than a bitboard and it does not matter at all here: the deepest
    // thing we ever do is a perft in the test suite.

    function isWhitePiece(p) { return !!p && p >= 'A' && p <= 'Z'; }
    function isBlackPiece(p) { return !!p && p >= 'a' && p <= 'z'; }
    function colorOf(p) { return !p ? '' : (isWhitePiece(p) ? 'w' : 'b'); }
    function typeOf(p) { return !p ? '' : p.toLowerCase(); }
    function other(color) { return color === 'w' ? 'b' : 'w'; }

    // ── Position ────────────────────────────────────────────────────────
    //
    // { board: Array(128), turn, castling: 'KQkq'|'-', ep: sq|-1,
    //   halfmove, fullmove }
    //
    // `ep` follows the modern convention used by Stockfish's FEN output: it
    // is set only when an enemy pawn actually sits next to the pushed pawn.
    // Always setting it (the older reading of the FEN spec) would make two
    // otherwise identical positions compare unequal and quietly suppress
    // legitimate threefold claims.

    var START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

    function emptyBoard() {
        var b = new Array(128);
        for (var i = 0; i < 128; i++) b[i] = '';
        return b;
    }

    function clonePos(pos) {
        return {
            board: pos.board.slice(),
            turn: pos.turn,
            castling: pos.castling,
            ep: pos.ep,
            halfmove: pos.halfmove,
            fullmove: pos.fullmove,
        };
    }

    function fromFEN(fen) {
        var parts = String(fen || '').trim().split(/\s+/);
        if (parts.length < 4) return null;

        var board = emptyBoard();
        var rows = parts[0].split('/');
        if (rows.length !== 8) return null;

        for (var r = 0; r < 8; r++) {
            // FEN writes rank 8 first; our rank index counts up from rank 1.
            var rank = 7 - r;
            var file = 0;
            var row = rows[r];
            for (var i = 0; i < row.length; i++) {
                var c = row[i];
                if (c >= '1' && c <= '8') {
                    file += parseInt(c, 10);
                } else if ('pnbrqkPNBRQK'.indexOf(c) >= 0) {
                    if (file > 7) return null;
                    board[sqOf(file, rank)] = c;
                    file++;
                } else {
                    return null;
                }
            }
            if (file !== 8) return null;
        }

        var turn = parts[1] === 'b' ? 'b' : (parts[1] === 'w' ? 'w' : null);
        if (!turn) return null;

        var castling = parts[2] === '-' ? '-' : parts[2];
        if (castling !== '-' && !/^K?Q?k?q?$/.test(castling)) return null;

        var ep = parts[3] === '-' ? -1 : fromAlg(parts[3]);
        if (parts[3] !== '-' && ep < 0) return null;

        var half = parts.length > 4 ? parseInt(parts[4], 10) : 0;
        var full = parts.length > 5 ? parseInt(parts[5], 10) : 1;

        return {
            board: board,
            turn: turn,
            castling: castling || '-',
            ep: ep,
            halfmove: isNaN(half) ? 0 : half,
            fullmove: isNaN(full) || full < 1 ? 1 : full,
        };
    }

    function toFEN(pos) {
        var out = [];
        for (var rank = 7; rank >= 0; rank--) {
            var run = 0;
            var row = '';
            for (var file = 0; file < 8; file++) {
                var p = pos.board[sqOf(file, rank)];
                if (!p) { run++; continue; }
                if (run) { row += run; run = 0; }
                row += p;
            }
            if (run) row += run;
            out.push(row);
        }
        return out.join('/') + ' ' + pos.turn + ' ' + (pos.castling || '-') +
            ' ' + (pos.ep >= 0 ? toAlg(pos.ep) : '-') +
            ' ' + pos.halfmove + ' ' + pos.fullmove;
    }

    function newGame() { return fromFEN(START_FEN); }

    // The only entry point anything off the wire should use: parse AND
    // sanity-check, null on either failure. Making the safe path the short
    // path is deliberate — a caller who forgets to validate gets a position
    // that plays illegal moves without ever raising.
    function fromWireFEN(fen) {
        var pos = fromFEN(fen);
        return (pos && isLegalPosition(pos)) ? pos : null;
    }

    // Repetition key: everything that defines "the same position" under the
    // repetition rule — placement, side to move, castling rights, ep square.
    // Deliberately NOT the clocks.
    function positionKey(pos) {
        var f = toFEN(pos).split(' ');
        return f[0] + ' ' + f[1] + ' ' + f[2] + ' ' + f[3];
    }

    // Is this a position that could actually arise in a game?
    //
    // fromFEN only checks that the text is well-formed. That is enough for a
    // position we built ourselves, but games carry a FEN checkpoint on the
    // wire so they survive the room's rolling archive, and that FEN arrives
    // from a machine we do not control. The dangerous one is a forged en
    // passant square: pawn capture generation trusts `ep`, so an ep square
    // with no pawn behind it lets a pawn take diagonally onto an empty
    // square and teleport. Every client would accept it identically, so
    // there is no desync to expose the corruption — it has to be rejected
    // at the door.
    function isLegalPosition(pos) {
        if (!pos || !pos.board) return false;

        var kings = { w: 0, b: 0 }, men = { w: 0, b: 0 }, pawns = { w: 0, b: 0 };
        for (var sq = 0; sq < 128; sq++) {
            if (!onBoard(sq)) { sq += 7; continue; }
            var p = pos.board[sq];
            if (!p) continue;
            var c = colorOf(p), t = typeOf(p);
            men[c]++;
            if (t === 'k') kings[c]++;
            if (t === 'p') {
                pawns[c]++;
                // A pawn on the first or last rank is unreachable — it would
                // have promoted.
                var r = rankOf(sq);
                if (r === 0 || r === 7) return false;
            }
        }
        if (kings.w !== 1 || kings.b !== 1) return false;
        if (men.w > 16 || men.b > 16) return false;
        if (pawns.w > 8 || pawns.b > 8) return false;

        // The side that just moved cannot have left its own king in check.
        if (inCheck(pos, other(pos.turn))) return false;

        if (pos.ep >= 0) {
            if (!onBoard(pos.ep)) return false;
            var epRank = pos.turn === 'w' ? 5 : 2;
            if (rankOf(pos.ep) !== epRank) return false;
            if (pos.board[pos.ep]) return false;                 // must be empty
            var pawnSq = pos.turn === 'w' ? pos.ep - 16 : pos.ep + 16;
            var fromSq = pos.turn === 'w' ? pos.ep + 16 : pos.ep - 16;
            var wantPawn = pos.turn === 'w' ? 'p' : 'P';
            if (pos.board[pawnSq] !== wantPawn) return false;     // the pusher
            if (pos.board[fromSq]) return false;                  // where it came from
        }

        if (!(pos.halfmove >= 0) || !(pos.fullmove >= 1)) return false;
        return true;
    }

    function findKing(pos, color) {
        var target = color === 'w' ? 'K' : 'k';
        for (var sq = 0; sq < 128; sq++) {
            if (!onBoard(sq)) { sq += 7; continue; }
            if (pos.board[sq] === target) return sq;
        }
        return -1;
    }

    // ── Attack detection ────────────────────────────────────────────────
    // "Is `sq` attacked by `color`?" — used for check, for castling (the
    // king may not start, cross, or land on an attacked square), and for
    // legality filtering.

    function isAttacked(pos, sq, color) {
        var b = pos.board, i, from, p;

        // Pawns. A white pawn on X attacks X+15 and X+17, so `sq` is
        // attacked by a white pawn sitting on sq-15 or sq-17.
        var pawnFrom = color === 'w' ? [sq - 15, sq - 17] : [sq + 15, sq + 17];
        var pawn = color === 'w' ? 'P' : 'p';
        for (i = 0; i < 2; i++) {
            from = pawnFrom[i];
            if (onBoard(from) && b[from] === pawn) return true;
        }

        // Knights.
        var knight = color === 'w' ? 'N' : 'n';
        for (i = 0; i < KNIGHT_HOPS.length; i++) {
            from = sq + KNIGHT_HOPS[i];
            if (onBoard(from) && b[from] === knight) return true;
        }

        // King (adjacency — matters for "kings may not touch").
        var king = color === 'w' ? 'K' : 'k';
        for (i = 0; i < KING_HOPS.length; i++) {
            from = sq + KING_HOPS[i];
            if (onBoard(from) && b[from] === king) return true;
        }

        // Sliders. Walk outward; the first occupied square on a ray is the
        // only one that can attack along it.
        var straight = color === 'w' ? ['R', 'Q'] : ['r', 'q'];
        var diagonal = color === 'w' ? ['B', 'Q'] : ['b', 'q'];
        var sets = [[ROOK_RAYS, straight], [BISHOP_RAYS, diagonal]];
        for (var s = 0; s < sets.length; s++) {
            var rays = sets[s][0], hitters = sets[s][1];
            for (i = 0; i < rays.length; i++) {
                from = sq + rays[i];
                while (onBoard(from)) {
                    p = b[from];
                    if (p) {
                        if (p === hitters[0] || p === hitters[1]) return true;
                        break;
                    }
                    from += rays[i];
                }
            }
        }
        return false;
    }

    function inCheck(pos, color) {
        var k = findKing(pos, color);
        if (k < 0) return false;          // king-less positions only exist in tests
        return isAttacked(pos, k, other(color));
    }

    // ── Move generation ─────────────────────────────────────────────────
    //
    // A move is { from, to, promo, flags } where flags is one of
    // '' | 'ep' | 'castle' | 'double'. Everything else (captured piece, SAN)
    // is derived on demand so a move stays cheap to put on the wire.

    function mv(from, to, promo, flags) {
        return { from: from, to: to, promo: promo || '', flags: flags || '' };
    }

    function pseudoMoves(pos, color) {
        var b = pos.board, moves = [], i, to, p;

        for (var from = 0; from < 128; from++) {
            if (!onBoard(from)) { from += 7; continue; }
            p = b[from];
            if (!p || colorOf(p) !== color) continue;
            var t = typeOf(p);

            if (t === 'p') {
                var dir = color === 'w' ? 16 : -16;
                var startRank = color === 'w' ? 1 : 6;
                var lastRank = color === 'w' ? 7 : 0;

                to = from + dir;
                if (onBoard(to) && !b[to]) {
                    if (rankOf(to) === lastRank) {
                        pushPromotions(moves, from, to);
                    } else {
                        moves.push(mv(from, to));
                        // Double push only from the home rank, and only if
                        // BOTH squares are clear.
                        var two = from + dir * 2;
                        if (rankOf(from) === startRank && onBoard(two) && !b[two]) {
                            moves.push(mv(from, two, '', 'double'));
                        }
                    }
                }
                // Captures, including en passant.
                var caps = [from + dir - 1, from + dir + 1];
                for (i = 0; i < 2; i++) {
                    to = caps[i];
                    if (!onBoard(to)) continue;
                    // Guard the wrap: from a-file, from+dir-1 lands on the
                    // previous rank's h-file, which IS on-board.
                    if (Math.abs(fileOf(to) - fileOf(from)) !== 1) continue;
                    if (b[to] && colorOf(b[to]) === other(color)) {
                        if (rankOf(to) === lastRank) pushPromotions(moves, from, to);
                        else moves.push(mv(from, to));
                    } else if (!b[to] && to === pos.ep) {
                        moves.push(mv(from, to, '', 'ep'));
                    }
                }
                continue;
            }

            if (t === 'n' || t === 'k') {
                var hops = t === 'n' ? KNIGHT_HOPS : KING_HOPS;
                for (i = 0; i < hops.length; i++) {
                    to = from + hops[i];
                    if (!onBoard(to)) continue;
                    if (b[to] && colorOf(b[to]) === color) continue;
                    moves.push(mv(from, to));
                }
                continue;
            }

            var rays = t === 'b' ? BISHOP_RAYS : (t === 'r' ? ROOK_RAYS : QUEEN_RAYS);
            for (i = 0; i < rays.length; i++) {
                to = from + rays[i];
                while (onBoard(to)) {
                    if (b[to]) {
                        if (colorOf(b[to]) !== color) moves.push(mv(from, to));
                        break;
                    }
                    moves.push(mv(from, to));
                    to += rays[i];
                }
            }
        }

        addCastles(pos, color, moves);
        return moves;
    }

    function pushPromotions(moves, from, to) {
        // Under-promotion is legal and occasionally the only winning move,
        // so all four are generated — never just the queen.
        var promos = ['q', 'r', 'b', 'n'];
        for (var i = 0; i < promos.length; i++) moves.push(mv(from, to, promos[i]));
    }

    function addCastles(pos, color, moves) {
        var b = pos.board;
        var rights = pos.castling || '-';
        var homeRank = color === 'w' ? 0 : 7;
        var kingSq = sqOf(4, homeRank);
        if (b[kingSq] !== (color === 'w' ? 'K' : 'k')) return;
        // Cannot castle out of check.
        if (isAttacked(pos, kingSq, other(color))) return;

        var kSide = color === 'w' ? 'K' : 'k';
        var qSide = color === 'w' ? 'Q' : 'q';
        var rook = color === 'w' ? 'R' : 'r';

        if (rights.indexOf(kSide) >= 0 && b[sqOf(7, homeRank)] === rook) {
            if (!b[sqOf(5, homeRank)] && !b[sqOf(6, homeRank)] &&
                !isAttacked(pos, sqOf(5, homeRank), other(color)) &&
                !isAttacked(pos, sqOf(6, homeRank), other(color))) {
                moves.push(mv(kingSq, sqOf(6, homeRank), '', 'castle'));
            }
        }
        if (rights.indexOf(qSide) >= 0 && b[sqOf(0, homeRank)] === rook) {
            // b1/b8 must be empty but may be attacked — the king never
            // crosses it. Getting this wrong is the classic castling bug.
            if (!b[sqOf(1, homeRank)] && !b[sqOf(2, homeRank)] && !b[sqOf(3, homeRank)] &&
                !isAttacked(pos, sqOf(3, homeRank), other(color)) &&
                !isAttacked(pos, sqOf(2, homeRank), other(color))) {
                moves.push(mv(kingSq, sqOf(2, homeRank), '', 'castle'));
            }
        }
    }

    // Which castling rights does touching `sq` destroy? Applies to the piece
    // MOVING from a square and to a piece CAPTURED on one — a rook taken on
    // h8 ends black's kingside right just as surely as moving it would.
    function rightsLostAt(sq) {
        if (sq === sqOf(4, 0)) return 'KQ';
        if (sq === sqOf(4, 7)) return 'kq';
        if (sq === sqOf(0, 0)) return 'Q';
        if (sq === sqOf(7, 0)) return 'K';
        if (sq === sqOf(0, 7)) return 'q';
        if (sq === sqOf(7, 7)) return 'k';
        return '';
    }

    function stripRights(castling, lost) {
        if (!lost) return castling;
        var out = '';
        for (var i = 0; i < castling.length; i++) {
            if (castling[i] !== '-' && lost.indexOf(castling[i]) < 0) out += castling[i];
        }
        return out || '-';
    }

    // Is a double-pushed pawn on `to` actually capturable en passant? Only
    // then does the ep square go into the FEN. We check for an adjacent
    // enemy pawn and stop there — the same convention Stockfish uses. A
    // pinned neighbour technically cannot capture, but treating that as
    // "no ep square" diverges from every other tool's FEN.
    function epCapturable(board, to, moverColor) {
        var enemyPawn = moverColor === 'w' ? 'p' : 'P';
        var sides = [to - 1, to + 1];
        for (var i = 0; i < 2; i++) {
            var s = sides[i];
            if (!onBoard(s)) continue;
            if (Math.abs(fileOf(s) - fileOf(to)) !== 1) continue;
            if (board[s] === enemyPawn) return true;
        }
        return false;
    }

    function makeMove(pos, move) {
        var next = clonePos(pos);
        var b = next.board;
        var piece = b[move.from];
        var color = colorOf(piece);
        var captured = b[move.to];

        b[move.to] = piece;
        b[move.from] = '';

        if (move.flags === 'ep') {
            // The captured pawn is beside the destination, not on it.
            var gone = color === 'w' ? move.to - 16 : move.to + 16;
            captured = b[gone];
            b[gone] = '';
        }

        if (move.promo) {
            b[move.to] = color === 'w' ? move.promo.toUpperCase() : move.promo.toLowerCase();
        }

        if (move.flags === 'castle') {
            // King has already moved; bring the rook round it.
            var rank = rankOf(move.to);
            if (fileOf(move.to) === 6) {
                b[sqOf(5, rank)] = b[sqOf(7, rank)];
                b[sqOf(7, rank)] = '';
            } else {
                b[sqOf(3, rank)] = b[sqOf(0, rank)];
                b[sqOf(0, rank)] = '';
            }
        }

        next.castling = stripRights(
            stripRights(next.castling, rightsLostAt(move.from)),
            rightsLostAt(move.to));

        next.ep = (move.flags === 'double' && epCapturable(b, move.to, color))
            ? (color === 'w' ? move.from + 16 : move.from - 16)
            : -1;

        // Fifty-move clock resets on any pawn move or capture.
        next.halfmove = (typeOf(piece) === 'p' || captured) ? 0 : pos.halfmove + 1;
        if (color === 'b') next.fullmove = pos.fullmove + 1;
        next.turn = other(color);
        return next;
    }

    // Legal = pseudo-legal that does not leave your own king attacked.
    function legalMoves(pos) {
        var pseudo = pseudoMoves(pos, pos.turn);
        var out = [];
        for (var i = 0; i < pseudo.length; i++) {
            var after = makeMove(pos, pseudo[i]);
            if (!inCheck(after, pos.turn)) out.push(pseudo[i]);
        }
        return out;
    }

    // ── Wire format ─────────────────────────────────────────────────────
    // UCI-ish: "e2e4", "e7e8q". Four or five characters, no ambiguity, no
    // dependence on the position to parse. SAN is display-only — putting it
    // on the wire would mean a client that disagrees about legality also
    // disagrees about how to READ the move.

    function moveToUci(move) {
        return toAlg(move.from) + toAlg(move.to) + (move.promo || '');
    }

    // Resolves a UCI string against the position's legal moves, so it
    // returns null for anything illegal, malformed, or hostile.
    function uciToMove(pos, uci) {
        if (typeof uci !== 'string' || uci.length < 4 || uci.length > 5) return null;
        var from = fromAlg(uci.slice(0, 2));
        var to = fromAlg(uci.slice(2, 4));
        if (from < 0 || to < 0) return null;
        var promo = uci.length === 5 ? uci[4].toLowerCase() : '';
        if (promo && 'qrbn'.indexOf(promo) < 0) return null;

        var moves = legalMoves(pos);
        for (var i = 0; i < moves.length; i++) {
            var m = moves[i];
            if (m.from === from && m.to === to && (m.promo || '') === promo) return m;
        }
        return null;
    }

    // ── SAN (display only) ──────────────────────────────────────────────

    function toSAN(pos, move) {
        var piece = pos.board[move.from];
        var t = typeOf(piece);
        var after = makeMove(pos, move);
        var suffix = '';
        if (inCheck(after, pos.turn === 'w' ? 'b' : 'w')) {
            suffix = legalMoves(after).length === 0 ? '#' : '+';
        }

        if (move.flags === 'castle') {
            return (fileOf(move.to) === 6 ? 'O-O' : 'O-O-O') + suffix;
        }

        var captured = !!pos.board[move.to] || move.flags === 'ep';

        if (t === 'p') {
            var s = captured ? FILES[fileOf(move.from)] + 'x' : '';
            s += toAlg(move.to);
            if (move.promo) s += '=' + move.promo.toUpperCase();
            return s + suffix;
        }

        // Disambiguate against every OTHER legal move of the same piece type
        // landing on the same square — by file if that is unique, else rank,
        // else both (three queens on one diagonal is rare but legal).
        var rivals = [];
        var legal = legalMoves(pos);
        for (var i = 0; i < legal.length; i++) {
            var m = legal[i];
            if (m.to === move.to && m.from !== move.from &&
                typeOf(pos.board[m.from]) === t) rivals.push(m);
        }
        var disambig = '';
        if (rivals.length) {
            var sameFile = rivals.some(function (m) { return fileOf(m.from) === fileOf(move.from); });
            var sameRank = rivals.some(function (m) { return rankOf(m.from) === rankOf(move.from); });
            if (!sameFile) disambig = FILES[fileOf(move.from)];
            else if (!sameRank) disambig = String(rankOf(move.from) + 1);
            else disambig = toAlg(move.from);
        }

        return t.toUpperCase() + disambig + (captured ? 'x' : '') + toAlg(move.to) + suffix;
    }

    // ── Terminal state ──────────────────────────────────────────────────
    //
    // `history` is every position key seen in the game INCLUDING the current
    // one, so a third occurrence means the array holds three copies.
    //
    // Note which draws are automatic and which are claims: stalemate,
    // checkmate and insufficient material end the game on their own.
    // Threefold and fifty-move are claimable under FIDE rules — we settle
    // them automatically because there is no arbiter in a chat room and a
    // game nobody can end is worse than one that ends slightly early.

    function insufficientMaterial(pos) {
        var pieces = [];
        for (var sq = 0; sq < 128; sq++) {
            if (!onBoard(sq)) { sq += 7; continue; }
            var p = pos.board[sq];
            if (!p) continue;
            var t = typeOf(p);
            if (t === 'k') continue;
            if (t === 'p' || t === 'r' || t === 'q') return false;
            pieces.push({ t: t, color: colorOf(p), light: (fileOf(sq) + rankOf(sq)) % 2 === 1 });
        }
        if (pieces.length === 0) return true;                       // K vs K
        if (pieces.length === 1) return true;                       // K+minor vs K
        if (pieces.length === 2) {
            // K+B vs K+B is drawn only with same-coloured bishops.
            if (pieces[0].t === 'b' && pieces[1].t === 'b' &&
                pieces[0].color !== pieces[1].color &&
                pieces[0].light === pieces[1].light) return true;
        }
        return false;
    }

    function status(pos, history) {
        var check = inCheck(pos, pos.turn);
        var moves = legalMoves(pos);

        if (moves.length === 0) {
            if (check) {
                return {
                    over: true, check: true, reason: 'checkmate',
                    result: pos.turn === 'w' ? '0-1' : '1-0',
                    winner: other(pos.turn),
                };
            }
            return { over: true, check: false, reason: 'stalemate', result: '1/2-1/2', winner: '' };
        }

        if (insufficientMaterial(pos)) {
            return { over: true, check: check, reason: 'insufficient', result: '1/2-1/2', winner: '' };
        }
        if (pos.halfmove >= 100) {
            return { over: true, check: check, reason: 'fifty', result: '1/2-1/2', winner: '' };
        }
        if (Array.isArray(history) && history.length) {
            var key = positionKey(pos);
            var seen = 0;
            for (var i = 0; i < history.length; i++) if (history[i] === key) seen++;
            if (seen >= 3) {
                return { over: true, check: check, reason: 'repetition', result: '1/2-1/2', winner: '' };
            }
        }

        return { over: false, check: check, reason: check ? 'check' : '', result: null, winner: '' };
    }

    // ── PGN ─────────────────────────────────────────────────────────────
    // Real export, because anyone who actually plays chess will want the
    // game out — and a game played over Soulseek being loadable in Lichess
    // is the whole joke.

    function toPGN(ucis, tags, startFen) {
        var pos = startFen ? fromFEN(startFen) : newGame();
        if (!pos) return '';
        var header = [];
        var t = tags || {};
        var seven = ['Event', 'Site', 'Date', 'Round', 'White', 'Black', 'Result'];
        var defaults = {
            Event: 'SoulSync Arcade', Site: 'Soulseek', Date: '????.??.??',
            Round: '-', White: '?', Black: '?', Result: '*',
        };
        for (var i = 0; i < seven.length; i++) {
            var k = seven[i];
            header.push('[' + k + ' "' + String(t[k] != null ? t[k] : defaults[k]).replace(/"/g, "'") + '"]');
        }
        if (startFen && startFen !== START_FEN) {
            header.push('[SetUp "1"]');
            header.push('[FEN "' + startFen + '"]');
        }

        var body = [];
        for (var j = 0; j < (ucis || []).length; j++) {
            var move = uciToMove(pos, ucis[j]);
            if (!move) break;                     // stop at the first illegal move
            var san = toSAN(pos, move);
            if (pos.turn === 'w') body.push(pos.fullmove + '.');
            else if (j === 0) body.push(pos.fullmove + '...');   // black-to-move start
            body.push(san);
            pos = makeMove(pos, move);
        }
        var result = t.Result != null ? String(t.Result) : '*';
        body.push(result);

        // Wrap at 80 columns, the PGN convention.
        var lines = [], line = '';
        for (var w = 0; w < body.length; w++) {
            if (line && (line.length + 1 + body[w].length) > 80) { lines.push(line); line = ''; }
            line += (line ? ' ' : '') + body[w];
        }
        if (line) lines.push(line);

        return header.join('\n') + '\n\n' + lines.join('\n') + '\n';
    }

    window.ChessEngine = {
        START_FEN: START_FEN,
        newGame: newGame,
        fromFEN: fromFEN,
        fromWireFEN: fromWireFEN,
        isLegalPosition: isLegalPosition,
        toFEN: toFEN,
        positionKey: positionKey,
        legalMoves: legalMoves,
        makeMove: makeMove,
        inCheck: inCheck,
        isAttacked: isAttacked,
        status: status,
        toSAN: toSAN,
        toPGN: toPGN,
        moveToUci: moveToUci,
        uciToMove: uciToMove,
        toAlg: toAlg,
        fromAlg: fromAlg,
        onBoard: onBoard,
        fileOf: fileOf,
        rankOf: rankOf,
        sqOf: sqOf,
        colorOf: colorOf,
        typeOf: typeOf,
    };
})();
