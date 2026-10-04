/**
 * Album reassign on Library v2 runs upstream's flow and modal
 * (`artist-detail/-ui/reassign-modal.tsx`); only the album it names differs.
 *
 * The album is a lib2 row and says so — `lib2:<id>`. The service refuses a
 * bare id on purpose, because the hint this flow writes is consumed against
 * `lib2_track_files` and a legacy id would quietly resolve to a different track.
 */
export function reassignSubject(albumId: number | string): string {
  return `lib2:${albumId}`;
}
