/**
 * Rotating playlists (#2039).
 *
 * A rotating playlist's downloads are removed once the song leaves the
 * playlist. The Expired Download Cleaner does the removing, so everything it
 * keeps (played, favourited, still in another playlist) is still kept. This
 * file reads the server's preview and turns it into the words the settings
 * sheet and the confirm show.
 */

export interface RotationPreview {
  rotating?: boolean;
  /** false for request lists and Liked Songs */
  allowed?: boolean;
  cleaner_enabled?: boolean;
  maintenance_enabled?: boolean;
  dry_run?: boolean;
  rotation_grace?: string;
  /** false: a play does not keep a song that left a rotating playlist (#2047) */
  keep_played?: boolean;
  /** false when the playlist's track list is empty or unreadable */
  membership_known?: boolean;
  downloads?: number;
  left?: number;
  removing?: number;
  waiting?: number;
  kept?: number;
  kept_reasons?: Record<string, number>;
}

export async function fetchRotationPreview(
  playlistId: number | string,
): Promise<RotationPreview | null> {
  try {
    const res = await fetch(`/api/mirrored-playlists/${playlistId}/rotation`);
    if (!res.ok) return null;
    const data = (await res.json()) as RotationPreview & { error?: string };
    return data.error ? null : data;
  } catch {
    return null;
  }
}

const REASONS: ReadonlyArray<[string, string]> = [
  ['other_playlist', 'in another playlist'],
  ['played', 'played'],
  ['curated', 'favourited or rated'],
  ['unknown', 'play count unknown'],
  ['older', 'from before a library rebuild'],
  ['unverified', 'favourites not checked yet'],
];

const WAITS: Readonly<Record<string, string>> = {
  none: 'on the next cleaner run',
  '1d': 'a day after they leave',
  '3d': '3 days after they leave',
  '1w': 'a week after they leave',
};

const songs = (n: number) => `${n} downloaded ${n === 1 ? 'song has' : 'songs have'}`;

/** what has left this playlist and what happens to it, in one line */
export function rotationSummary(p: RotationPreview | null): string {
  if (!p) return '';
  if (!p.downloads) return 'Nothing downloaded from this playlist yet';
  if (!p.membership_known) return "Can't tell which songs have left until it has tracks";
  const left = p.left ?? 0;
  if (!left) return 'No downloaded songs have left this playlist';
  const parts = [`${songs(left)} left`];
  const removing = p.removing ?? 0;
  parts.push(`${removing} ${p.rotating ? 'will' : 'would'} be removed`);
  const kept = p.kept ?? 0;
  if (kept) {
    const why = REASONS.filter(([key]) => (p.kept_reasons?.[key] ?? 0) > 0).map(
      ([, label]) => label,
    );
    parts.push(why.length ? `${kept} kept (${why.join(', ')})` : `${kept} kept`);
  }
  return parts.join(' · ');
}

/** how and when removals happen, only worth saying once it is rotating */
export function rotationStatus(p: RotationPreview | null): string {
  if (!p || !p.rotating) return '';
  if (p.maintenance_enabled === false) {
    return 'Library maintenance is paused, so nothing is removed until it is back on';
  }
  if (p.cleaner_enabled === false) {
    return 'The Expired Download Cleaner is off, so nothing is removed';
  }
  const when = WAITS[p.rotation_grace ?? '1d'] ?? WAITS['1d'];
  return p.dry_run === false
    ? `Files are deleted automatically, ${when}`
    : `Dry run is on: removals wait in the cleaner's findings for you to approve, ${when}`;
}

/** the confirm shown before a playlist is made rotating */
export function rotationConfirmMessage(name: string, p: RotationPreview | null): string {
  const lines = [
    `Songs "${name}" downloaded are removed from your library once they leave the playlist.`,
    p?.keep_played === false
      ? 'Anything you favourited, rated or still have in another playlist is kept. Played songs are not.'
      : 'Anything you played, favourited, rated or still have in another playlist is kept.',
  ];
  if (p && p.membership_known && (p.left ?? 0) > 0) {
    lines.push(`${songs(p.left ?? 0)} already left, ${p.removing ?? 0} would be removed.`);
  }
  if (p && p.cleaner_enabled === false) {
    lines.push('This turns on the Expired Download Cleaner.');
  }
  lines.push(
    p && p.dry_run === false
      ? 'Dry run is off, so files are deleted automatically.'
      : "Dry run is on, so removals wait in the cleaner's findings for you to approve.",
  );
  return lines.join(' ');
}
