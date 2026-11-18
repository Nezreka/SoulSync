/**
 * Keep Best over many duplicate findings at once (#2179).
 *
 * A bulk Keep Best can't act on every finding by itself: copies named
 * differently need a person to say they are one recording, and a finding
 * whose other copies are all protected has nothing to remove. Those stay
 * open, marked "Needs you". The server says why per row (`needs_you`, from
 * core.library2.duplicate_review.keep_best_blockers); these are the words,
 * and the counts the bulk prompt shows before anything runs.
 */
import type { RepairFinding } from './-tools.types';

export type NeedsYouReason = 'same_recording' | 'playlist' | 'protected';

/** why a duplicate finding needs you, in the order a person deals with it */
export const NEEDS_YOU_ORDER: readonly NeedsYouReason[] = [
  'same_recording',
  'playlist',
  'protected',
];

/** short, for the row badge's line */
export const NEEDS_YOU_SHORT: Record<NeedsYouReason, string> = {
  same_recording: 'confirm it is the same recording',
  playlist: 'a copy is in a playlist the kept copy is not in',
  protected: 'every other copy is protected',
};

/** the counted form, for the prompt and the run's toast */
function countedWords(reason: string, n: number): string {
  const one = n === 1;
  switch (reason) {
    case 'same_recording':
      return `${n} named differently, so you confirm ${one ? 'it is' : 'each is'} the same recording`;
    case 'playlist':
      return `${n} with a copy in a playlist the kept copy is not in`;
    case 'protected':
      return `${n} where every other copy is protected`;
    default:
      return `${n} for another reason`;
  }
}

/** the row's reasons, worded, or null when bulk Keep Best can act on it */
export function needsYouLine(finding: Pick<RepairFinding, 'needs_you'>): string | null {
  const reasons = finding.needs_you?.reasons || [];
  if (!reasons.length) return null;
  const words = NEEDS_YOU_ORDER.filter((reason) => reasons.includes(reason)).map(
    (reason) => NEEDS_YOU_SHORT[reason],
  );
  return words.length ? `Needs you: ${words.join(', and ')}` : null;
}

export interface KeepBestTally {
  /** findings in the run */
  total: number;
  /** findings Keep Best can act on by itself */
  ready: number;
  /** findings left for you, by reason (one finding can have two) */
  needs: Record<string, number>;
  /** findings left for you, each counted once */
  held: number;
}

/** what a bulk Keep Best over loaded rows does, with moving playlist entries
 *  on or off and the differently named pairs confirmed or not */
export function tallyFindings(
  findings: readonly Pick<RepairFinding, 'needs_you'>[],
  options: { move: boolean; confirm: boolean },
): KeepBestTally {
  const needs: Record<string, number> = {};
  let held = 0;
  for (const finding of findings) {
    const base = (options.move ? finding.needs_you?.with_move : finding.needs_you?.reasons) || [];
    const reasons = options.confirm ? base.filter((reason) => reason !== 'same_recording') : base;
    if (reasons.length) held += 1;
    for (const reason of reasons) needs[reason] = (needs[reason] || 0) + 1;
  }
  return { total: findings.length, ready: findings.length - held, needs, held };
}

/** the server's summary, in the same shape (no confirming from there: the
 *  pairs are not in front of you) */
export interface KeepBestSummary {
  total: number;
  ready: number;
  ready_with_move: number;
  needs_you: Record<string, number>;
  needs_you_with_move: Record<string, number>;
}

export function tallySummary(summary: KeepBestSummary, move: boolean): KeepBestTally {
  const ready = move ? summary.ready_with_move : summary.ready;
  return {
    total: summary.total,
    ready,
    needs: move ? summary.needs_you_with_move : summary.needs_you,
    held: summary.total - ready,
  };
}

/** "3 named differently ..., 2 with a copy in a playlist ..." */
export function needsYouList(needs: Record<string, number> | null | undefined): string[] {
  const entries = Object.entries(needs || {}).filter(([, n]) => n > 0);
  const known = NEEDS_YOU_ORDER.filter((reason) => (needs || {})[reason]);
  const other = entries.filter(([reason]) => !known.includes(reason as NeedsYouReason));
  return [
    ...known.map((reason) => countedWords(reason, (needs || {})[reason])),
    ...other.map(([reason, n]) => countedWords(reason, n)),
  ];
}

/** the run's "M need you" clause: ", 4 need you and stay open (3 ..., 1 ...)" */
export function needsYouClause(
  needs: Record<string, number> | null | undefined,
  held: number,
): string {
  if (!held) return '';
  const list = needsYouList(needs);
  return `, ${held} need${held === 1 ? 's' : ''} you and stay${held === 1 ? 's' : ''} open${
    list.length ? ` (${list.join('; ')})` : ''
  }`;
}

/** a pair to look at before confirming it is one recording */
export interface RecordingPair {
  id: number;
  copies: Array<{ title: string; artist: string; detail: string }>;
}

/** the differently named findings in a selection, as the prompt lists them */
export function recordingPairs(findings: readonly RepairFinding[]): RecordingPair[] {
  return findings
    .filter((finding) => finding.needs_you?.reasons.includes('same_recording'))
    .map((finding) => {
      const tracks = Array.isArray(finding.details?.tracks)
        ? (finding.details?.tracks as Array<Record<string, unknown>>)
        : [];
      return {
        id: finding.id,
        copies: tracks.map((track) => ({
          title: String(track.title || ''),
          artist: String(track.artist || ''),
          detail: [
            track.format ? String(track.format).toUpperCase() : '',
            track.album ? String(track.album) : '',
          ]
            .filter(Boolean)
            .join(' · '),
        })),
      };
    });
}
