/**
 * Why a download candidate was taken or passed over — the vocabulary of
 * core/downloads/decisions.py, shared by the candidate inspector and the
 * track-detail "why this file" block.
 */

export interface CandidateDecision {
  accepted: boolean;
  code: string;
  detail: string;
  stage: string;
  score: number | null;
}

/** Short pill text per code. The detail line carries the specifics. */
export const DECISION_LABELS: Record<string, string> = {
  preview: 'preview clip',
  duration_mismatch: 'wrong length',
  artist_mismatch: 'wrong artist',
  artist_unverified: 'artist unverified',
  match_weak: 'weak match',
  outranked: 'outranked',
  version_conflict: 'wrong version',
  below_profile: 'below your profile',
  peer_queue: 'busy peer',
  quarantined: 'failed before',
  too_large: 'too large',
  blacklisted: 'blacklisted',
  duplicate: 'duplicate',
  unexplained: 'rejected',
};

export function decisionLabel(code: string): string {
  return DECISION_LABELS[code] ?? code.replace(/_/g, ' ');
}

export function decisionPill(
  row: { decision?: CandidateDecision; duration?: number },
  expectedMs?: number,
): string {
  const d = row.decision;
  if (!d) return '';
  if (d.code === 'duration_mismatch' && expectedMs && row.duration) {
    return row.duration < expectedMs ? 'too short' : 'too long';
  }
  if (d.code === 'version_conflict') {
    const found = /^(.+?) version, asked for the original/.exec(d.detail);
    if (found) return `version: ${found[1]}`;
    const wanted = /^asked for the (.+?) version/.exec(d.detail);
    if (wanted) return `not ${wanted[1]}`;
  }
  return decisionLabel(d.code);
}

/** "9 weak match · 4 wrong version" — most common first, as the server sends it. */
export function rejectionSummary(counts: Record<string, number>): string {
  return Object.entries(counts)
    .map(([code, n]) => `${n} ${decisionLabel(code)}`)
    .join(' · ');
}
