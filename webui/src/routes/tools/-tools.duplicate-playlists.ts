/**
 * The words for a duplicate copy's playlists, and the Keep Best summary the
 * confirm dialog shows (#2172). Pure, so the rules read the same in the
 * finding and in the dialog, and can be tested without a page.
 */
import type { KeepBestCopy } from './-tools.api';

import { listWords } from './-tools.finding-copy';

const quoted = (names: readonly string[]) => listWords(names.map((name) => `"${name}"`));

/** the playlists that still keep this copy after the plan */
export function playlistsLeft(copy: KeepBestCopy): string[] {
  const settled = new Set([
    ...copy.ignored_playlists,
    ...copy.playlists_covered,
    ...copy.playlist_moves,
  ]);
  return copy.playlists.filter((name) => !settled.has(name));
}

/** one line per thing worth saying about a copy's playlists */
export function copyPlaylistNotes(copy: KeepBestCopy): string[] {
  const notes: string[] = [];
  if (copy.action === 'keep') {
    if (copy.playlists.length) notes.push(`In ${quoted(copy.playlists)}. Stays as it is.`);
    return notes;
  }
  if (copy.playlists_covered.length) {
    notes.push(
      `In ${quoted(copy.playlists_covered)} with the kept copy, so no playlist loses the song.`,
    );
  }
  if (copy.playlist_moves.length) {
    notes.push(`Its entry in ${quoted(copy.playlist_moves)} moves to the kept copy, same place.`);
  }
  if (copy.ignored_playlists.length) {
    notes.push(`In ${quoted(copy.ignored_playlists)}, which you set to never protect a copy.`);
  }
  const left = playlistsLeft(copy);
  switch (copy.playlists_blocked) {
    case 'same_server_item':
      notes.push(
        "Your media server has one entry for both copies and SoulSync can't tell which file it plays, so this copy stays.",
      );
      break;
    case 'not_in_kept':
      notes.push(
        `Stays: it is in ${quoted(left)}, which the kept copy is not in. Turn on moving playlist entries to keep ${left.length === 1 ? 'that playlist' : 'those playlists'} whole and remove it.`,
      );
      break;
    case 'kept_not_on_server':
      notes.push(
        `Stays: the kept copy is not on your media server yet, so the entry in ${quoted(left)} can't move to it. Rescan the server, then try again.`,
      );
      break;
    case 'move_failed':
      notes.push(`Stays: its entry in ${quoted(left)} could not be moved.`);
      break;
    default:
      break;
  }
  return notes;
}

const fileName = (path: string) => path.split(/[\\/]/).pop() || path;

/** what Keep Best will do, for the confirm dialog */
export function keepBestSummary(copies: readonly KeepBestCopy[]): string {
  const kept = copies.find((copy) => copy.action === 'keep');
  const removed = copies.filter((copy) => copy.action === 'remove');
  const stays = copies.filter((copy) => copy.action === 'protected');
  const parts: string[] = [];
  if (kept) parts.push(`Keeps ${fileName(kept.path)}.`);
  parts.push(
    removed.length
      ? `Moves ${removed.length} cop${removed.length === 1 ? 'y' : 'ies'} to recoverable quarantine.`
      : 'Removes nothing: every other copy is protected.',
  );
  const moves = removed.flatMap((copy) => copy.playlist_moves);
  if (moves.length) {
    parts.push(
      `Points ${moves.length === 1 ? 'its entry' : 'their entries'} in ${quoted([...new Set(moves)])} at the kept copy first, in the same place.`,
    );
  }
  if (stays.length) {
    parts.push(`${stays.length} cop${stays.length === 1 ? 'y stays' : 'ies stay'}, as listed.`);
  }
  return parts.join(' ');
}
