import type { LibraryV2AcquisitionRejection } from '../-library-v2.api';

/** One human-readable line for a bundle-matching conflict (F-12).
 *
 *  The review queue exists so an operator can resolve an ambiguous bundle, but
 *  the list used to render the rejection *code* and — when the payload happened
 *  to carry one — a path. `missing_expected_track` carries no path, so the most
 *  common conflict read as a bare "missing expected track" with no hint which
 *  track was missing, which is the one thing the operator needs to know before
 *  assigning files.
 *
 *  Every code emitted by `core/acquisition/bundle_matching.py::match_bundle`
 *  therefore gets its own identifying detail here. Values arrive as parsed JSON,
 *  so each one is narrowed before use: a nested object must never reach the DOM
 *  as "[object Object]", which is exactly what the previous `String(...)` calls
 *  would have produced.
 */

export interface RejectionLine {
  label: string;
  detail: string;
}

const text = (value: unknown): string =>
  typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : '';

const count = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const percent = (value: unknown): string => {
  const ratio = count(value);
  return ratio === null ? '' : `${Math.round(ratio * 100)}%`;
};

const join = (...parts: Array<string>): string => parts.filter(Boolean).join(' · ');

const readable = (value: unknown): string => text(value).replaceAll('_', ' ');

/** Where the conflict is, in the terms the operator is looking at. */
function detailFor(rejection: LibraryV2AcquisitionRejection): string {
  const path = text(rejection.relative_path);
  switch (text(rejection.code)) {
    case 'missing_expected_track': {
      // Position first, because the assignment dropdown is ordered by it. Disc 1
      // is the default for a single-disc bundle and only adds noise.
      const disc = count(rejection.disc_number);
      const track = count(rejection.track_number);
      return (
        join(
          disc !== null && disc > 1 ? `Disc ${disc}` : '',
          track === null ? '' : `Track ${track}`,
          text(rejection.expected_title),
        ) || text(rejection.expected_key)
      );
    }
    case 'unmatched_file':
      return path;
    case 'ambiguous_position':
      return join(path, readable(rejection.reason));
    case 'ambiguous_title': {
      const similarity = percent(rejection.similarity);
      return join(path, similarity && `${similarity} similar`);
    }
    case 'low_confidence': {
      const confidence = percent(rejection.confidence);
      return join(path, confidence && `${confidence} confidence`);
    }
    // `no_expected_tracklist` is about the whole bundle and has nothing to point
    // at; an unknown future code still shows its path when it carries one.
    default:
      return path;
  }
}

export function describeRejection(rejection: LibraryV2AcquisitionRejection): RejectionLine {
  const code = readable(rejection.code);
  return { label: code || 'Unresolved match', detail: code ? detailFor(rejection) : '' };
}
