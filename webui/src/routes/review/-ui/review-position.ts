export function formatTrackPosition(
  disc: number | string | null | undefined,
  track: number | string | null | undefined,
  numberingMode?: string,
  displayTrack?: number | string | null,
): string {
  const number = displayTrack === '' || displayTrack == null ? track || '—' : displayTrack;
  return numberingMode === 'digital' ? String(number) : `${disc || 1}.${number}`;
}
