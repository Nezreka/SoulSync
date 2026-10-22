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
