/** Pure edit-diff helpers for Library v2 metadata correction modals.
 *
 * Kept React-free so the diff/validation logic (where number parsing bugs
 * hide) is unit-testable without rendering. The modal stays a thin shell over
 * these functions and the existing `updateLibraryV2MetadataOverrides` endpoint.
 */

export interface TrackEditOriginal {
  title: string | null;
  track_number: number | null;
  disc_number: number | null;
}

export interface TrackEditForm {
  title: string;
  trackNumber: string;
  discNumber: string;
}

export interface MetadataEditResult {
  /** Only the fields that actually changed — sent as the override `set`. */
  values: Record<string, unknown>;
  /** False when a required field is empty or a number is malformed. */
  valid: boolean;
}

function parseOptionalCount(raw: string): { value: number | null; valid: boolean } {
  const trimmed = raw.trim();
  if (trimmed === '') return { value: null, valid: true };
  const num = Number(trimmed);
  if (!Number.isInteger(num) || num < 0) return { value: null, valid: false };
  return { value: num, valid: true };
}

/** Diff a track-edit form against the track's current effective metadata. */
export function computeTrackEditValues(
  original: TrackEditOriginal,
  form: TrackEditForm,
): MetadataEditResult {
  const values: Record<string, unknown> = {};
  let valid = true;

  const title = form.title.trim();
  if (title === '') {
    valid = false;
  } else if (title !== (original.title ?? '')) {
    values.title = title;
  }

  const track = parseOptionalCount(form.trackNumber);
  if (!track.valid) {
    valid = false;
  } else if (track.value !== null && track.value !== original.track_number) {
    values.track_number = track.value;
  }

  const disc = parseOptionalCount(form.discNumber);
  if (!disc.valid) {
    valid = false;
  } else if (disc.value !== null && disc.value !== original.disc_number) {
    values.disc_number = disc.value;
  }

  return { values, valid };
}
