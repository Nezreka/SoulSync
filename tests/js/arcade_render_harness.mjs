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
