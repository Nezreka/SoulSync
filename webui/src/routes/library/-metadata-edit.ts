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
  bpm: number | null;
  explicit: boolean | null;
  style: string | null;
  mood: string | null;
}

export interface TrackEditForm {
  title: string;
  trackNumber: string;
  discNumber: string;
  bpm: string;
  /** '' = unknown/no override, 'yes'/'no' = explicit true/false. */
  explicitFlag: '' | 'yes' | 'no';
  style: string;
  mood: string;
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

/** Like `parseOptionalCount` but allows fractional values (bpm). */
function parseOptionalNumber(raw: string): { value: number | null; valid: boolean } {
  const trimmed = raw.trim();
  if (trimmed === '') return { value: null, valid: true };
  const num = Number(trimmed);
  if (!Number.isFinite(num) || num < 0) return { value: null, valid: false };
  return { value: num, valid: true };
}

/** Nullable free-text field: empty clears to `null`, matching the album/artist
 *  forms' style/mood/label convention (unlike track/disc number, which treat
 *  an emptied field as "no change" rather than an explicit clear). */
function diffOptionalText(
  values: Record<string, unknown>,
  field: string,
  raw: string,
  original: string | null,
): void {
  const trimmed = raw.trim();
  if (trimmed !== (original ?? '')) {
    values[field] = trimmed || null;
  }
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

  const bpm = parseOptionalNumber(form.bpm);
  if (!bpm.valid) {
    valid = false;
  } else if (bpm.value !== null && bpm.value !== original.bpm) {
    values.bpm = bpm.value;
  }

  const initialExplicitFlag =
    original.explicit === true ? 'yes' : original.explicit === false ? 'no' : '';
  if (form.explicitFlag !== initialExplicitFlag) {
    values.explicit = form.explicitFlag === '' ? null : form.explicitFlag === 'yes';
  }

  diffOptionalText(values, 'style', form.style, original.style);
  diffOptionalText(values, 'mood', form.mood, original.mood);

  return { values, valid };
}
