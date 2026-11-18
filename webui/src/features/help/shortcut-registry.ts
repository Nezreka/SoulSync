/**
 * Every keyboard shortcut SoulSync has, in one registry (plan 4.8; the plan
 * calls it shortcuts.ts, but shortcuts.tsx is the view and wins the import). The
 * Shortcuts view renders it, and the palette can show a hint beside an
 * action with shortcutHint(id).
 *
 * Real keys only. Each entry names the file and the line of code that
 * handles it (`source`), and shortcut-registry.test.ts reads that file and fails if
 * the handler is gone, so a removed shortcut can't linger here.
 *
 * Keys are written once, platform neutral: 'Mod' is Ctrl, or ⌘ on a Mac.
 */

export type ShortcutGroupId = 'anywhere' | 'player' | 'theater' | 'pages' | 'help';

export interface Shortcut {
  id: string;
  group: ShortcutGroupId;
  label: string;
  /** each inner list is one chord, e.g. ['Mod', 'K']; several chords are alternatives or a pair */
  keys: string[][];
  /** 'or' reads "? or F1"; 'pair' sets two chords side by side, like ← → */
  join?: 'or' | 'pair';
  /** where it works, when that is not everywhere */
  where?: string;
  /** the handler: a file under webui/ and a string that file must contain */
  source: { file: string; has: string };
}

export interface ShortcutGroup {
  id: ShortcutGroupId;
  label: string;
  /** one line under the label */
  note?: string;
}

export const SHORTCUT_GROUPS: ShortcutGroup[] = [
  { id: 'anywhere', label: 'Anywhere' },
  { id: 'player', label: 'Player', note: 'While something is playing.' },
  { id: 'theater', label: 'Now Playing', note: 'While the expanded player is open.' },
  { id: 'pages', label: 'On a page' },
  { id: 'help', label: 'Help & Docs' },
];

const HOST = 'src/features/help/host.tsx';
const PLAYER = 'static/media-player.js';
const THEATER = 'static/media-player-v2.js';

export const SHORTCUTS: Shortcut[] = [
  // anywhere
  {
    id: 'help.open',
    group: 'anywhere',
    label: 'Open help',
    keys: [['?'], ['F1']],
    join: 'or',
    source: { file: HOST, has: "event.key === 'F1'" },
  },
  {
    id: 'help.search',
    group: 'anywhere',
    label: 'Search help, settings and actions',
    keys: [['Mod', 'K']],
    source: { file: HOST, has: "(event.key === 'k' || event.key === 'K')" },
  },
  {
    id: 'close',
    group: 'anywhere',
    label: 'Close a panel or window',
    keys: [['Esc']],
    source: { file: HOST, has: "event.key === 'Escape'" },
  },

  // player (handlePlayerKeyboardShortcuts)
  {
    id: 'player.play',
    group: 'player',
    label: 'Play or pause',
    keys: [['Space']],
    source: { file: PLAYER, has: "case ' ':" },
  },
  {
    id: 'player.seek',
    group: 'player',
    label: 'Back or forward 5 seconds',
    keys: [['←'], ['→']],
    join: 'pair',
    source: { file: PLAYER, has: 'audioPlayer.currentTime - 5' },
  },
  {
    id: 'player.volume',
    group: 'player',
    label: 'Volume up or down',
    keys: [['↑'], ['↓']],
    join: 'pair',
    source: { file: PLAYER, has: 'audioPlayer.volume + 0.05' },
  },
  {
    id: 'player.mute',
    group: 'player',
    label: 'Mute',
    keys: [['M']],
    source: { file: PLAYER, has: 'handleNpMuteToggle();' },
  },
  {
    id: 'player.next',
    group: 'player',
    label: 'Next track',
    keys: [['N']],
    source: { file: PLAYER, has: 'playNextInQueue();' },
  },
  {
    id: 'player.previous',
    group: 'player',
    label: 'Previous track',
    keys: [['P']],
    source: { file: PLAYER, has: 'playPreviousInQueue();' },
  },

  // the expanded Now Playing player (npv2HandleKeys)
  {
    id: 'theater.loop',
    group: 'theater',
    label: 'Loop a part (A to B)',
    keys: [['L']],
    source: { file: THEATER, has: 'npv2AbToggle();' },
  },
  {
    id: 'theater.speed',
    group: 'theater',
    label: 'Slower or faster',
    keys: [['['], [']']],
    join: 'pair',
    source: { file: THEATER, has: 'npv2CycleSpeed(1);' },
  },
  {
    id: 'theater.visuals',
    group: 'theater',
    label: 'Next or previous visual',
    keys: [['V'], ['Shift', 'V']],
    join: 'or',
    source: { file: THEATER, has: 'npv2PrevTheme(); else npv2CycleTheme();' },
  },
  {
    id: 'theater.immersive',
    group: 'theater',
    label: 'Visuals only',
    keys: [['F']],
    source: { file: THEATER, has: 'npv2SetVisualsMode(!npv2VisualsModeOn());' },
  },
  {
    id: 'theater.keys',
    group: 'theater',
    label: 'The player’s own shortcut list',
    keys: [['?']],
    source: { file: THEATER, has: 'npv2ToggleShortcuts();' },
  },

  // on one page
  {
    id: 'calendar.step',
    group: 'pages',
    label: 'Previous or next week or month',
    where: 'Calendar',
    keys: [['←'], ['→']],
    join: 'pair',
    source: {
      file: 'src/routes/calendar/-ui/calendar-page.tsx',
      has: "if (e.key === 'ArrowLeft') go(-1);",
    },
  },
  {
    id: 'calendar.today',
    group: 'pages',
    label: 'Back to today',
    where: 'Calendar',
    keys: [['T']],
    source: { file: 'src/routes/calendar/-ui/calendar-page.tsx', has: 'goToday();' },
  },
  {
    id: 'settings.search',
    group: 'pages',
    label: 'Search settings',
    where: 'Settings',
    keys: [['/']],
    source: { file: 'src/features/settings/shell.ts', has: "if (e.key !== '/' || e.ctrlKey" },
  },
  {
    id: 'audiobooks.search',
    group: 'pages',
    label: 'Search audiobooks',
    where: 'Audiobooks',
    keys: [['/']],
    source: {
      file: 'src/routes/audiobooks/-ui/audiobook-search-bar.tsx',
      has: "event.key === '/'",
    },
  },

  // help & docs
  {
    id: 'docs.search',
    group: 'help',
    label: 'Search the docs',
    where: 'Help & Docs',
    keys: [['/']],
    source: { file: 'static/docs.js', has: "if (e.key !== '/') return;" },
  },
  {
    id: 'help.back',
    group: 'help',
    label: 'Back, in this help panel',
    keys: [['Alt', '←']],
    source: {
      file: 'src/features/help/sheet.tsx',
      has: "event.key === 'ArrowLeft' && event.altKey",
    },
  },
  {
    id: 'tour.step',
    group: 'help',
    label: 'Next or previous tour step',
    keys: [['←'], ['→']],
    join: 'pair',
    source: { file: 'src/features/help/tours/layer.tsx', has: "event.key === 'ArrowRight'" },
  },
];

// ── platform ────────────────────────────────────────────────────────────

export function isMac(): boolean {
  if (typeof navigator === 'undefined') return false;
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  const platform = nav.userAgentData?.platform || nav.platform || nav.userAgent || '';
  return /mac|iphone|ipad|ipod/i.test(platform);
}

/** a phone or tablet with no keyboard to speak of */
export function isTouchOnly(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(hover: none) and (pointer: coarse)').matches
  );
}

const MAC_KEYS: Record<string, string> = { Mod: '⌘', Alt: '⌥', Shift: '⇧' };
const PC_KEYS: Record<string, string> = { Mod: 'Ctrl' };

/** what a key cap says: 'Mod' → 'Ctrl' or '⌘' */
export function keyLabel(key: string, mac = isMac()): string {
  return (mac ? MAC_KEYS : PC_KEYS)[key] ?? key;
}

const SPOKEN: Record<string, string> = {
  '←': 'Left arrow',
  '→': 'Right arrow',
  '↑': 'Up arrow',
  '↓': 'Down arrow',
  '?': 'Question mark',
  '/': 'Slash',
  '[': 'Left bracket',
  ']': 'Right bracket',
  Esc: 'Escape',
};

/** what a screen reader hears: "Control K", "Command K", "Left arrow or Right arrow" */
export function spokenKeys(shortcut: Pick<Shortcut, 'keys' | 'join'>, mac = isMac()): string {
  const word = (key: string) =>
    key === 'Mod'
      ? mac
        ? 'Command'
        : 'Control'
      : key === 'Alt' && mac
        ? 'Option'
        : (SPOKEN[key] ?? key);
  const chords = shortcut.keys.map((chord) => chord.map(word).join(' '));
  return chords.join(shortcut.join === 'pair' ? ', ' : ' or ');
}

/** "Ctrl K" / "⌘K", for a hint beside a palette action; '' for an unknown id */
export function shortcutHint(id: string, mac = isMac()): string {
  const found = SHORTCUTS.find((s) => s.id === id);
  if (!found) return '';
  const chord = (keys: string[]) => keys.map((k) => keyLabel(k, mac)).join(mac ? '' : ' ');
  return found.keys.map(chord).join(found.join === 'or' ? ' or ' : ' ');
}

export function shortcutsIn(group: ShortcutGroupId): Shortcut[] {
  return SHORTCUTS.filter((s) => s.group === group);
}
