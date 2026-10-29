// The same field family drives numbering controls and display groups.
export const NUMBERING_FIELDS: readonly string[] = [
  'track_number',
  'disc_number',
  'total_discs',
  'library_track_number',
  'library_disc_number',
];

export function formatTrackPosition(
  disc: number | string | null | undefined,
  track: number | string | null | undefined,
  numberingMode?: string,
  displayTrack?: number | string | null,
): string {
  const number = displayTrack === '' || displayTrack == null ? track || '—' : displayTrack;
  return numberingMode === 'digital' || Number(disc || 1) === 1
    ? String(number)
    : `${disc}.${number}`;
}
