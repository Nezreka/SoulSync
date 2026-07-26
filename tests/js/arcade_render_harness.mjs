// Behavioral harness for the Arcade renderers in chat.js.
// Run under node; exits non-zero with a message on any failure.
//
// Two contracts are being defended here.
//
// XSS: a Soulseek username is remote input from a stranger, and it lands in a
// game card, the player line, the sidebar row and every title attribute. The
// same rule as the message renderer applies — escape first, always.
//
// Honesty: the board is a fold, so what it draws has to match what the fold
// says. A "your move" badge on the wrong player, or a Join button on a game
// you are already sitting in, would have people making moves the room will
// reject with no explanation.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, '..', '..', 'webui', 'static');
const read = f => readFileSync(join(staticDir, f), 'utf8');

// Minimal environment: chat.js touches document + MutationObserver at load.
globalThis.document = {
    readyState: 'complete',
    hidden: false,
    getElementById: () => null,
    querySelector: () => null,
    addEventListener: () => {},
    dispatchEvent: () => {},
};
globalThis.MutationObserver = class { observe() {} };
globalThis.window = {};

// The Arcade reads both of these off window and hides itself if either is
// missing, so load them the way the page does — before chat.js.
(0, eval)(read('chat-hash.js'));
(0, eval)(read('chess-engine.js'));
(0, eval)(read('chat-games.js'));
(0, eval)(read('chat.js'));

const CP = globalThis.window.ChatPage;
const E = globalThis.window.ChessEngine;
const CG = globalThis.window.ChatGames;

let failures = 0;
function check(name, cond, got) {
    if (!cond) { failures++; console.error(`FAIL: ${name}\n  got: ${String(got).slice(0, 400)}`); }
}

const T0 = Date.parse('2026-07-25T12:00:00Z');
const ev = (username, p, at = T0) => ({ username, timestamp: new Date(at).toISOString(), p });

// Put a protocol log in place and point chat.js at it, the way a real room
// would after the poll ingests carriers.
function setRoom(events, self) {
    CP._testSetSelf(self);
    CP._testSetState({
        room: 'SoulSync', homeRoom: 'SoulSync', view: 'room',
        canSend: true, isAdmin: true,
        protocolLog: events.map(e => Object.assign({ room: 'SoulSync' }, e)),
        arcade: { game: null, sel: -1, promo: null, flip: false },
    });
}
const game = (events, id) => CG.reduceGames(
    events.map(e => Object.assign({ room: 'SoulSync' }, e)), Date.now()).games[id];

// ── XSS: hostile usernames ──────────────────────────────────────────────
const NASTY = '<img src=x onerror=alert(1)>';
{
    const evs = [
        ev(NASTY, { k: 'gm.new', g: 'aaaa', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'aaaa' }, T0 + 1000),
    ];
    setRoom(evs, 'kazimir');
    const lobby = CP._arcLobbyHtml();
    check('lobby: hostile username escaped', !lobby.includes('<img src=x'), lobby);
    check('lobby: username still shown, escaped', lobby.includes('&lt;img'), lobby);

    const side = CP._arcSidebarHtml();
    check('sidebar: hostile username escaped', !side.includes('<img src=x'), side);

    CP._testSetState({ arcade: { game: 'aaaa', sel: -1, promo: null, flip: false } });
    const board = CP._arcBoardHtml(game(evs, 'aaaa'));
    check('board: hostile username escaped', !board.includes('<img src=x'), board);
    // It also goes into a title="" attribute, which is a different escape context.
    check('board: no attribute breakout', !/title="[^"]*<img/.test(board), board);
}

// ── the board reflects the fold ─────────────────────────────────────────
{
    const evs = [
        ev('boulder', { k: 'gm.new', g: 'bbbb', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'bbbb' }, T0 + 1000),
    ];
    setRoom(evs, 'boulder');
    CP._testSetState({ arcade: { game: 'bbbb', sel: -1, promo: null, flip: false } });
    const g = game(evs, 'bbbb');
    const board = CP._arcBoardHtml(g);

    check('board: 64 squares', (board.match(/data-chat-arc-sq=/g) || []).length === 64, board.length);
    check('board: white to move sees "Your move"', board.includes('Your move'), board);
    check('board: resign offered to a seated player', board.includes('data-chat-arc-resign'), board);
    check('board: no Join button for someone already seated',
          !board.includes('data-chat-arc-join'), board);
    // Both kings present as glyphs.
    check('board: kings rendered', board.includes('♔') && board.includes('♚'), board);
    check('board: no moves yet', board.includes('no moves yet'), board);

    // The opponent's view of the SAME game must not claim it is their move.
    setRoom(evs, 'kazimir');
    CP._testSetState({ arcade: { game: 'bbbb', sel: -1, promo: null, flip: false } });
    const other = CP._arcBoardHtml(g);
    check('board: opponent is told who they are waiting on',
          other.includes('Waiting on boulder'), other);
    check('board: opponent is not told it is their move', !other.includes('Your move'), other);
}

// ── a spectator gets no controls ────────────────────────────────────────
{
    const evs = [
        ev('boulder', { k: 'gm.new', g: 'cccc', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'cccc' }, T0 + 1000),
    ];
    setRoom(evs, 'sella');
    CP._testSetState({ arcade: { game: 'cccc', sel: -1, promo: null, flip: false } });
    const board = CP._arcBoardHtml(game(evs, 'cccc'));
    check('spectator: cannot resign', !board.includes('data-chat-arc-resign'), board);
    check('spectator: cannot offer a draw', !board.includes('data-chat-arc-draw'), board);
    check('spectator: no join on a full game', !board.includes('data-chat-arc-join'), board);
}

// ── an open game invites exactly the right people ───────────────────────
{
    const open = [ev('boulder', { k: 'gm.new', g: 'dddd', v: 'chess' })];
    setRoom(open, 'kazimir');
    check('open game: a stranger is offered Join',
          CP._arcLobbyHtml().includes('data-chat-arc-join'), '');
    setRoom(open, 'boulder');
    check('open game: the creator is not offered Join against himself',
          !CP._arcLobbyHtml().includes('data-chat-arc-join'), '');

    const priv = [ev('boulder', { k: 'gm.new', g: 'eeee', v: 'chess', o: 'kazimir' })];
    setRoom(priv, 'sella');
    check('private game: an uninvited user gets no Join',
          !CP._arcLobbyHtml().includes('data-chat-arc-join'), '');
    setRoom(priv, 'kazimir');
    check('private game: the invited user gets Join',
          CP._arcLobbyHtml().includes('data-chat-arc-join'), '');
}

// ── read-only servers can watch but not play ────────────────────────────
{
    const evs = [ev('boulder', { k: 'gm.new', g: 'ffff', v: 'chess' })];
    setRoom(evs, 'kazimir');
    CP._testSetState({ canSend: false });
    const lobby = CP._arcLobbyHtml();
    check('read-only: no New game button', !lobby.includes('data-chat-arc-new'), lobby);
    check('read-only: no Join button', !lobby.includes('data-chat-arc-join'), lobby);
    check('read-only: says why', lobby.includes('watch but not play'), lobby);
}

// ── finished and frozen games say so ────────────────────────────────────
{
    const mate = ['f2f3', 'e7e5', 'g2g4', 'd8h4'];
    let pos = E.newGame();
    const evs = [
        ev('boulder', { k: 'gm.new', g: 'gggg', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'gggg' }, T0 + 1000),
    ];
    mate.forEach((uci, i) => {
        const mover = pos.turn === 'w' ? 'boulder' : 'kazimir';
        pos = E.makeMove(pos, E.uciToMove(pos, uci));
        evs.push(ev(mover, { k: 'gm.move', g: 'gggg', n: i, m: uci, f: E.toFEN(pos) },
                    T0 + 2000 + i));
    });
    setRoom(evs, 'boulder');
    CP._testSetState({ arcade: { game: 'gggg', sel: -1, promo: null, flip: false } });
    const g = game(evs, 'gggg');
    const board = CP._arcBoardHtml(g);
    check('mate: winner named', board.includes('kazimir wins by checkmate'), board);
    check('mate: no resign button after the game ended',
          !board.includes('data-chat-arc-resign'), board);
    // The move list is real algebraic notation, replayed from the start.
    check('mate: SAN move list', board.includes('Qh4#'), board);

    const frozen = [
        ev('boulder', { k: 'gm.new', g: 'hhhh', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'hhhh' }, T0 + 1000),
        ev('boulder', { k: 'gm.move', g: 'hhhh', n: 0, m: 'e2e4',
                        f: '4k3/8/8/8/8/8/8/4K2R b - - 0 1' }, T0 + 2000),
    ];
    setRoom(frozen, 'boulder');
    CP._testSetState({ arcade: { game: 'hhhh', sel: -1, promo: null, flip: false } });
    const fb = CP._arcBoardHtml(game(frozen, 'hhhh'));
    check('desync: explains itself rather than showing a wrong board',
          fb.includes('disagreed'), fb);
}

// ── an adopted game numbers its moves honestly ──────────────────────────
{
    // `moves` only collects what arrived AFTER this client picked the game
    // up, so replaying them from the opening position would claim the game
    // began with them. This one is at move 5.
    const mid = 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3';
    const evs = [
        ev('kazimir', { k: 'gm.move', g: 'jjjj', v: 'chess', n: 7, m: 'e7e5', f: mid }),
        ev('boulder', { k: 'gm.move', g: 'jjjj', n: 8, m: 'g1f3' }, T0 + 1000),
    ];
    setRoom(evs, 'boulder');
    CP._testSetState({ arcade: { game: 'jjjj', sel: -1, promo: null, flip: false } });
    const board = CP._arcBoardHtml(game(evs, 'jjjj'));
    check('adopted: move numbered from the real move, not from 1',
          board.includes('>3.<') || board.includes('3.</span>'), board);
    check('adopted: does not claim the game opened with this move',
          !/chat-arc-moveno">1\./.test(board), board);
    check('adopted: says why the opening is missing', board.includes('rolled past'), board);
}

// ── the sidebar badges whose move it is ─────────────────────────────────
{
    const evs = [
        ev('boulder', { k: 'gm.new', g: 'iiii', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'iiii' }, T0 + 1000),
    ];
    setRoom(evs, 'boulder');
    const side = CP._arcSidebarHtml();
    check('sidebar: unread count when it is your move',
          side.includes('chat-chan--unread'), side);
    setRoom(evs, 'kazimir');
    check('sidebar: no unread count when it is not',
          !CP._arcSidebarHtml().includes('chat-chan--unread'), CP._arcSidebarHtml());
}

// ── PGN export is a real chess file ─────────────────────────────────────
{
    const evs = [
        ev('boulder', { k: 'gm.new', g: 'kkkk', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'kkkk' }, T0 + 1000),
    ];
    let pos = E.newGame();
    ['e2e4', 'e7e5', 'g1f3'].forEach((uci, i) => {
        const mover = pos.turn === 'w' ? 'boulder' : 'kazimir';
        pos = E.makeMove(pos, E.uciToMove(pos, uci));
        evs.push(ev(mover, { k: 'gm.move', g: 'kkkk', n: i, m: uci, f: E.toFEN(pos) },
                    T0 + 2000 + i));
    });
    setRoom(evs, 'boulder');
    const pgn = CP._arcPgn(game(evs, 'kkkk'));
    check('pgn: names the players', pgn.includes('[White "boulder"]') &&
          pgn.includes('[Black "kazimir"]'), pgn);
    check('pgn: real algebraic movetext', pgn.includes('1. e4 e5 2. Nf3'), pgn);
    check('pgn: says where it was played', pgn.includes('Soulseek'), pgn);
    check('pgn: a full game needs no SetUp tag', !pgn.includes('[SetUp'), pgn);

    // A game adopted mid-stream has a partial move list, so the PGN must
    // carry the position it starts from or it describes a different game.
    const mid = 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3';
    const part = [
        ev('kazimir', { k: 'gm.move', g: 'llll', v: 'chess', n: 7, m: 'e7e5', f: mid }),
        ev('boulder', { k: 'gm.move', g: 'llll', n: 8, m: 'g1f3' }, T0 + 1000),
    ];
    setRoom(part, 'boulder');
    const ppgn = CP._arcPgn(game(part, 'llll'));
    check('pgn: a partial game declares its start position',
          ppgn.includes('[SetUp "1"]') && ppgn.includes(mid), ppgn);
}

// ── the ladder ──────────────────────────────────────────────────────────
{
    const evs = [
        ev('boulder', { k: 'gm.new', g: 'mmmm', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'mmmm' }, T0 + 1),
        ev('kazimir', { k: 'gm.res', g: 'mmmm' }, T0 + 2),
    ];
    setRoom(evs, 'boulder');
    const lobby = CP._arcLobbyHtml();
    check('ladder: shown once there is a result', lobby.includes('Room ladder'), lobby);
    check('ladder: winner rated above the start', lobby.includes('1216'), lobby);
    check('ladder: explains what it is', lobby.includes('everyone starts at 1200'), lobby);

    // No results yet -> no ladder at all, rather than a table of 1200s.
    setRoom([ev('boulder', { k: 'gm.new', g: 'nnnn', v: 'chess' })], 'boulder');
    check('ladder: hidden until someone finishes a game',
          !CP._arcLobbyHtml().includes('Room ladder'), '');
}

// ── the reveal ──────────────────────────────────────────────────────────
{
    const evs = [
        ev('boulder', { k: 'gm.new', g: 'oooo', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'oooo' }, T0 + 1),
    ];
    setRoom(evs, 'boulder');
    CP._testSetState({ arcade: { game: 'oooo', sel: -1, promo: null, flip: false } });
    let board = CP._arcBoardHtml(game(evs, 'oooo'));
    check('reveal: counts the carriers this board came from',
          board.includes('folded from 2 room messages'), board);
    check('reveal: collapsed by default', !board.includes('chat-arc-reveal--open'), board);

    CP._testSetState({ arcade: { game: 'oooo', sel: -1, promo: null, flip: false, reveal: true } });
    board = CP._arcBoardHtml(game(evs, 'oooo'));
    check('reveal: opens to the raw carriers', board.includes('chat-arc-reveal--open'), board);
    check('reveal: shows the actual payload', board.includes('gm.new'), board);
    check('reveal: escapes the raw payload too', !board.includes('<img src=x'), board);

    // The count is per-game, not the whole room.
    const noisy = evs.concat([
        ev('sella', { k: 'gm.new', g: 'pppp', v: 'chess' }, T0 + 5),
        ev('sella', { k: 'jbx.vote', o: 'xyz' }, T0 + 6),
    ]);
    setRoom(noisy, 'boulder');
    CP._testSetState({ arcade: { game: 'oooo', sel: -1, promo: null, flip: false } });
    check('reveal: counts only this game\'s carriers',
          CP._arcBoardHtml(game(noisy, 'oooo')).includes('folded from 2 room messages'),
          CP._arcBoardHtml(game(noisy, 'oooo')));
}

// ── connect 4 shares the whole shell ────────────────────────────────────
{
    const evs = [
        ev('boulder', { k: 'gm.new', g: 'qqqq', v: 'connect4' }),
        ev('kazimir', { k: 'gm.join', g: 'qqqq' }, T0 + 1000),
    ];
    setRoom(evs, 'boulder');
    CP._testSetState({ arcade: { game: 'qqqq', sel: -1, promo: null, flip: false } });
    const g = game(evs, 'qqqq');
    const board = CP._arcBoardHtml(g);

    // Count the class attribute, not the class name: a playable cell also
    // carries chat-arc-c4cell--live and would be counted twice.
    check('c4: 42 cells', (board.match(/class="chat-arc-c4cell/g) || []).length === 42,
          (board.match(/class="chat-arc-c4cell/g) || []).length);
    check('c4: no chessboard', !board.includes('chat-arc-sq'), board.slice(0, 200));
    check('c4: your move names the action', board.includes('pick a column'), board);
    // Only 7 columns are clickable, not 42 cells: you drop into a column.
    const targets = new Set([...board.matchAll(/data-chat-arc-col="(\d)"/g)].map(m => m[1]));
    check('c4: every column is a target', targets.size === 7, [...targets].join(','));
    check('c4: the reveal works here too', board.includes('folded from'), board);
    check('c4: resign offered', board.includes('data-chat-arc-resign'), board);

    // The opponent gets no clickable columns at all.
    setRoom(evs, 'kazimir');
    CP._testSetState({ arcade: { game: 'qqqq', sel: -1, promo: null, flip: false } });
    const other = CP._arcBoardHtml(g);
    check('c4: not your move means nothing is clickable',
          !other.includes('data-chat-arc-col'), other);
    check('c4: told who we are waiting on', other.includes('Waiting on boulder'), other);
}

// ── a move is validated before it can reach the room ────────────────────
{
    // previewMove is what builds the checkpoint, and it is variant-agnostic:
    // if it cannot produce a position the move is never sent.
    const CGx = globalThis.window.ChatGames;
    const chess = { variant: 'chess', fen: E.START_FEN };
    check('preview: legal chess move produces a position',
          !!CGx.previewMove(chess, 'e2e4'), '');
    check('preview: illegal chess move produces nothing',
          CGx.previewMove(chess, 'e2e5') === null, '');
    const c4 = { variant: 'connect4', fen: '.'.repeat(42) + ' w' };
    check('preview: legal drop produces a position', !!CGx.previewMove(c4, '3'), '');
    check('preview: column off the board produces nothing',
          CGx.previewMove(c4, '9') === null, '');
    check('preview: a chess move in connect 4 produces nothing',
          CGx.previewMove(c4, 'e2e4') === null, '');
    check('preview: an unknown variant produces nothing',
          CGx.previewMove({ variant: 'calvinball', fen: 'x' }, '1') === null, '');
}

// ── you vs the room ─────────────────────────────────────────────────────
{
    const roomGame = ev('boulder', { k: 'gm.new', g: 'rrrr', v: 'chess', r: 1 });
    let pos = E.newGame();
    pos = E.makeMove(pos, E.uciToMove(pos, 'e2e4'));
    const evs = [roomGame,
        ev('boulder', { k: 'gm.move', g: 'rrrr', n: 0, m: 'e2e4', f: E.toFEN(pos) }, T0 + 1000)];

    // A room voter sees a ballot and can pick a move.
    setRoom(evs, 'kazimir');
    CP._testSetState({ arcade: { game: 'rrrr', sel: -1, promo: null, flip: false } });
    let board = CP._arcBoardHtml(game(evs, 'rrrr'));
    check('room: the seat reads as the room, not a username',
          board.includes('the room'), board);
    check('room: a voter is invited to pick', board.includes('pick a move to vote'), board);
    check('room: says what it takes to carry', board.includes('commits it'), board);

    // The human opponent gets no ballot — they are playing against the room.
    setRoom(evs, 'boulder');
    CP._testSetState({ arcade: { game: 'rrrr', sel: -1, promo: null, flip: false } });
    board = CP._arcBoardHtml(game(evs, 'rrrr'));
    check('room: the opponent is told they have no ballot',
          board.includes('no ballot for you'), board);
    check('room: the opponent cannot drag the room\'s pieces',
          !board.includes('draggable="true"'), board);

    // With votes in, the tally is shown in real notation.
    const voted = evs.concat([
        ev('kazimir', { k: 'gm.vote', g: 'rrrr', n: 1, m: 'e7e5' }, T0 + 2000),
    ]);
    setRoom(voted, 'sella');
    CP._testSetState({ arcade: { game: 'rrrr', sel: -1, promo: null, flip: false } });
    board = CP._arcBoardHtml(game(voted, 'rrrr'));
    check('room: ballot shows the move in algebraic', board.includes('>e5<'), board);
    check('room: ballot shows progress to the threshold', board.includes('1/2'), board);

    // A room game never reaches the ladder — one side is not a person.
    const done = voted.concat([ev('boulder', { k: 'gm.res', g: 'rrrr' }, T0 + 9000)]);
    setRoom(done, 'boulder');
    check('room: not rated', !CP._arcLobbyHtml().includes('Room ladder'),
          CP._arcLobbyHtml());
}

// ── sync + acknowledgement ──────────────────────────────────────────────
{
    let pos = E.newGame();
    const evs = [ev('boulder', { k: 'gm.new', g: 'ssss', v: 'chess' }),
                 ev('kazimir', { k: 'gm.join', g: 'ssss' }, T0 + 1000)];
    const push = (uci, i) => { const who = pos.turn === 'w' ? 'boulder' : 'kazimir';
        pos = E.makeMove(pos, E.uciToMove(pos, uci));
        evs.push(ev(who, { k: 'gm.move', g: 'ssss', n: i, m: uci, f: E.toFEN(pos) },
                    T0 + 2000 + i)); };
    push('e2e4', 0);

    // boulder moved and kazimir has not answered: not acknowledged.
    setRoom(evs, 'boulder');
    CP._testSetState({ arcade: { game: 'ssss', sel: -1, promo: null, flip: false } });
    let board = CP._arcBoardHtml(game(evs, 'ssss'));
    check('ack: waiting is shown honestly', board.includes('not acknowledged yet'), board);
    check('ack: a Sync button is offered', board.includes('data-chat-arc-sync'), board);

    // Their sync carrier doubles as a read receipt: it says how far along
    // they are, which proves they have our move — without them moving, and
    // without any dedicated acknowledgement message.
    const acked = evs.concat([ev('kazimir', { k: 'gm.sync', g: 'ssss', n: 1 }, T0 + 5000)]);
    setRoom(acked, 'boulder');
    CP._testSetState({ arcade: { game: 'ssss', sel: -1, promo: null, flip: false } });
    board = CP._arcBoardHtml(game(acked, 'ssss'));
    check('ack: their sync proves receipt', board.includes('has seen your move'), board);

    // Once they reply with a move it is our turn again, so there is nothing
    // left to confirm and the line goes away.
    push('e7e5', 1);
    setRoom(evs, 'boulder');
    CP._testSetState({ arcade: { game: 'ssss', sel: -1, promo: null, flip: false } });
    board = CP._arcBoardHtml(game(evs, 'ssss'));
    check('ack: nothing to confirm on our own turn',
          !board.includes('acknowledged') && !board.includes('has seen'), board);

    // A spectator is not shown either — it is not their game.
    setRoom(evs, 'sella');
    CP._testSetState({ arcade: { game: 'ssss', sel: -1, promo: null, flip: false } });
    board = CP._arcBoardHtml(game(evs, 'ssss'));
    check('ack: nothing for a spectator', !board.includes('acknowledged'), board);
    check('sync: no button for a spectator', !board.includes('data-chat-arc-sync'), board);
}

// ── a frozen game offers the only way out ───────────────────────────────
{
    const frozen = [ev('boulder', { k: 'gm.new', g: 'tttt', v: 'chess' }),
        ev('kazimir', { k: 'gm.join', g: 'tttt' }, T0 + 1000),
        ev('boulder', { k: 'gm.move', g: 'tttt', n: 0, m: 'e2e4',
                        f: '4k3/8/8/8/8/8/8/4K2R b - - 0 1' }, T0 + 2000)];
    setRoom(frozen, 'kazimir');
    CP._testSetState({ arcade: { game: 'tttt', sel: -1, promo: null, flip: true } });
    const board = CP._arcBoardHtml(game(frozen, 'tttt'));
    check('frozen: explains itself', board.includes('disagreed'), board);
    check('frozen: offers to accept their position',
          board.includes('data-chat-arc-accept'), board);
    check('frozen: does not offer a plain sync instead',
          !board.includes('data-chat-arc-sync'), board);
    check('frozen: no acknowledgement line', !board.includes('acknowledged'), board);
}

// ── battleship ──────────────────────────────────────────────────────────
{
    const H = globalThis.window.ChatHash;
    const mk = spec => { const b = new Array(100).fill('.');
        for (const [id, start, horiz, len] of spec)
            for (let i = 0; i < len; i++) b[start + (horiz ? i : i * 10)] = id;
        return b.join(''); };
    const FLEET = mk([['1', 0, true, 5], ['2', 10, true, 4], ['3', 20, true, 3],
                      ['4', 30, true, 3], ['5', 40, true, 2]]);

    const evs = [ev('boulder', { k: 'gm.new', g: 'bs01', v: 'battleship' }),
                 ev('kazimir', { k: 'gm.join', g: 'bs01' }, T0 + 1000)];

    // Setup: a player who has not committed gets the placement board.
    setRoom(evs, 'boulder');
    CP._testSetState({ arcade: { game: 'bs01', sel: -1, promo: null, flip: false } });
    let board = CP._arcBsBoardHtml(game(evs, 'bs01'));
    check('bs: setup offers a placement grid',
          board.includes('Lay out your fleet'), board);
    check('bs: 100 placement cells',
          (board.match(/data-chat-bs-place=/g) || []).length === 100, board.length);
    check('bs: the whole fleet is listed', ['Carrier', 'Battleship', 'Cruiser',
          'Submarine', 'Destroyer'].every(n => board.includes(n)), board);
    check('bs: says the layout stays local', board.includes('stays on this machine'), board);
    check('bs: cannot fire during setup', !board.includes('data-chat-bs-fire'), board);

    // Both committed: two grids, and only the player to move can fire.
    const salt = H.salt();
    const live = evs.concat([
        ev('boulder', { k: 'gm.move', g: 'bs01', v: 'battleship', n: 0,
                        m: 'c:' + H.commit(salt, FLEET), f: null }, T0 + 2000),
    ]);
    // Build the committed state honestly through the fold instead of by hand.
    const G2 = globalThis.window.ChatGames;
    let g0 = game(evs, 'bs01');
    const p1 = G2.previewMove(g0, 'c:' + H.commit(salt, FLEET), 'w');
    const evs2 = evs.concat([ev('boulder', { k: 'gm.move', g: 'bs01', v: 'battleship',
        n: 0, m: 'c:' + H.commit(salt, FLEET), f: p1.fen }, T0 + 2000)]);
    let g1 = game(evs2, 'bs01');
    const p2 = G2.previewMove(g1, 'c:' + H.commit('other', FLEET), 'b');
    const evs3 = evs2.concat([ev('kazimir', { k: 'gm.move', g: 'bs01', v: 'battleship',
        n: 1, m: 'c:' + H.commit('other', FLEET), f: p2.fen }, T0 + 3000)]);

    setRoom(evs3, 'boulder');
    CP._testSetState({ arcade: { game: 'bs01', sel: -1, promo: null, flip: false } });
    board = CP._arcBsBoardHtml(game(evs3, 'bs01'));
    check('bs: two grids once both fleets are in',
          (board.match(/chat-bs-grid/g) || []).length >= 2, board.length);
    check('bs: white is invited to shoot', board.includes('Your shot'), board);
    check('bs: firing targets exist', board.includes('data-chat-bs-fire'), board);

    // The opponent must not be able to fire on white's turn.
    setRoom(evs3, 'kazimir');
    CP._testSetState({ arcade: { game: 'bs01', sel: -1, promo: null, flip: false } });
    board = CP._arcBsBoardHtml(game(evs3, 'bs01'));
    check('bs: not your turn means no targets',
          !board.includes('data-chat-bs-fire'), board);

    // A spectator sees the game but never a fleet or a trigger.
    setRoom(evs3, 'sella');
    CP._testSetState({ arcade: { game: 'bs01', sel: -1, promo: null, flip: false } });
    board = CP._arcBsBoardHtml(game(evs3, 'bs01'));
    check('bs: spectators cannot fire', !board.includes('data-chat-bs-fire'), board);
    check('bs: spectators are not shown a fleet',
          !board.includes('chat-bs-cell--ship'), board);
}

// ── a withdrawn table leaves no trace in the lobby ──────────────────────
{
    const evs = [ev('boulder', { k: 'gm.new', g: 'wdrw', v: 'chess' }),
                 ev('boulder', { k: 'gm.cancel', g: 'wdrw' }, T0 + 5000)];
    setRoom(evs, 'boulder');
    const lobby = CP._arcLobbyHtml();
    check('withdrawn: gone from the lobby', !lobby.includes('wdrw'), lobby);
    check('withdrawn: not filed under Finished', !lobby.includes('Finished'), lobby);
    check('withdrawn: gone from the sidebar too',
          !CP._arcSidebarHtml().includes('wdrw'), CP._arcSidebarHtml());

    // A game that was actually PLAYED and ended still belongs in Finished.
    const played = [ev('boulder', { k: 'gm.new', g: 'dfin', v: 'chess' }),
                    ev('kazimir', { k: 'gm.join', g: 'dfin' }, T0 + 1000),
                    ev('boulder', { k: 'gm.res', g: 'dfin' }, T0 + 2000)];
    setRoom(played, 'boulder');
    check('resigned games are still listed', CP._arcLobbyHtml().includes('Finished'),
          CP._arcLobbyHtml());
}

// ── battleship placement: the board starts empty and rotation works ─────
{
    setRoom([], 'boulder');
    CP._testSetState({ arcade: { game: 'zz01', sel: -1, promo: null, flip: false } });
    const d = CP._bsDraft();
    check('place: the board starts EMPTY',
          d.board.replace(/\./g, '').length === 0, d.board);

    CP._bsPlaceAt(0);
    const carrier = [...CP._bsDraft().board].map((c, i) => c === '1' ? i : null).filter(v => v !== null);
    check('place: horizontal runs along the row',
          carrier.join(',') === '0,1,2,3,4', carrier.join(','));

    CP._bsDraft().horiz = false;
    CP._bsPlaceAt(20);
    const bship = [...CP._bsDraft().board].map((c, i) => c === '2' ? i : null).filter(v => v !== null);
    check('place: rotated runs down the column',
          bship.join(',') === '20,30,40,50', bship.join(','));

    CP._bsPlaceAt(0);
    check('place: clicking a placed ship picks it up',
          CP._bsDraft().board.indexOf('1') < 0, CP._bsDraft().board);

    // A ship that would hang off the edge is refused rather than wrapping.
    CP._bsDraft().horiz = true;
    const before = CP._bsDraft().board;
    CP._bsPlaceAt(7);                       // carrier of 5 from column h
    check('place: a ship cannot hang off the edge',
          CP._bsDraft().board === before, CP._bsDraft().board);
}

// ── the Arcade hides itself if its libraries are absent ─────────────────
{
    // chat.js must not throw when chess-engine.js / chat-games.js failed to
    // load — the rest of chat has to keep working.
    const isolated = { window: {} };
    vm.createContext(isolated);
    isolated.document = globalThis.document;
    isolated.MutationObserver = globalThis.MutationObserver;
    vm.runInContext(read('chat.js'), isolated);
    const bare = isolated.window.ChatPage;
    bare._testSetState({ room: 'SoulSync', homeRoom: 'SoulSync', view: 'room',
                         protocolLog: [], arcade: null });
    let threw = null;
    try { bare._arcSidebarHtml(); } catch (e) { threw = e; }
    check('no engine: sidebar renders nothing instead of throwing', threw === null, threw);
    check('no engine: sidebar is empty', bare._arcSidebarHtml() === '', bare._arcSidebarHtml());
}

if (failures) {
    console.error(`\n${failures} arcade render check(s) failed`);
    process.exit(1);
}
console.log('arcade render harness: all checks passed');
