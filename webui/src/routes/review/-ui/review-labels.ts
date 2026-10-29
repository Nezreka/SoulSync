import type { ReviewPlan, ReviewCaseSummary, ReviewDecision } from '../-review.api';

import { decisionOrder } from './review-album-model';

export const requireChoice = new Set([
  'reconciliation_extra',
  'reconciliation_duplicate',
  'reconciliation_intent',
  'reconciliation_release',
]);

export const emptyPlan: ReviewPlan = {
  ready: 0,
  manual: 0,
  destructive: 0,
  blocked: 0,
  total: 0,
  status: 'needs_review' as const,
  blocker_reasons: [] as string[],
  can_apply_ready: false,
  can_organize: false,
};

export const categoryOptions = [
  ['', 'All categories'],
  ['metadata', 'Metadata changes'],
  ['title_conventions', 'Title conventions'],
  ['title_issues', 'Title identity'],
  ['numbering', 'Numbering schemes'],
  ['release_conflicts', 'Release or edition'],
  ['audio_conflicts', 'Audio conflicts'],
  ['album_completeness', 'Album completeness'],
  ['duplicates', 'Duplicates'],
  ['extra_files', 'Extra files'],
] as const;

export type QueueMode = 'review' | 'ready' | 'files' | 'waiting' | 'all';

export type AlbumView = 'plan' | 'release' | 'tracks';

export type TrackView = 'issues' | 'release';

export const queueModes: Array<{ value: QueueMode; label: string; risk: string }> = [
  { value: 'review', label: 'Needs input', risk: 'manual' },
  { value: 'ready', label: 'Ready to apply', risk: 'ready' },
  { value: 'files', label: 'File actions', risk: 'destructive' },
  { value: 'waiting', label: 'Waiting', risk: 'waiting' },
  { value: 'all', label: 'All', risk: '' },
];

export function caseStatusSummary(item: ReviewCaseSummary): string {
  if (item.refreshing) return 'Reanalyzing this album · updated decisions will appear here';
  if (!item.plan) {
    return `${item.pending} pending action${item.pending === 1 ? '' : 's'}`;
  }
  const ordered = [...(item.plan.decisions || [])].sort(
    (left, right) => (decisionOrder[left.category] ?? 99) - (decisionOrder[right.category] ?? 99),
  );
  const primary =
    ordered.find(
      (decision) => decision.risk !== 'ready' && decision.category !== 'pending_fulfillment',
    ) ||
    ordered.find((decision) => decision.category !== 'pending_fulfillment') ||
    ordered[0];
  const status =
    primary?.category === 'apply_blockers'
      ? 'Needs attention'
      : primary?.category === 'pending_fulfillment'
        ? 'Waiting for download'
        : item.plan.status === 'waiting'
          ? 'Waiting for download'
          : item.plan.status === 'ready'
            ? 'Ready'
            : 'Needs input';
  const more = ordered.length > 1 ? ` · +${ordered.length - 1} more` : '';
  return `${status}${primary ? ` · ${primary.label}` : ''} · ${item.pending} action${item.pending === 1 ? '' : 's'}${more}`;
}

export function decisionInstruction(
  decision: ReviewDecision,
  numberingMode?: string,
  multipleDiscs = false,
): string {
  switch (decision.category) {
    case 'release_conflicts':
      return 'Choose the release this album should follow';
    case 'album_completeness':
      return decision.label === 'Submit track requests'
        ? 'Request the wanted tracks before applying changes'
        : 'Choose whether this folder represents the complete album';
    case 'numbering':
      if (decision.label === 'Disc numbering') return decision.summary || 'Update disc numbering';
      if (!multipleDiscs) return 'Renumber tracks to match the selected release';
      return numberingMode === 'digital'
        ? 'Switch this album to continuous track numbering'
        : "Use the selected release's disc and track numbering";
    case 'audio_conflicts':
      return `Confirm ${decision.action_count} uncertain track assignment${decision.action_count === 1 ? '' : 's'}`;
    case 'title_issues':
      return `Resolve ${decision.action_count} recording title difference${decision.action_count === 1 ? '' : 's'}`;
    case 'duplicates':
      return `Choose which ${decision.action_count === 1 ? 'duplicate file' : 'duplicate files'} to keep`;
    case 'extra_files':
      return `Decide what to do with ${decision.action_count} unmatched file${decision.action_count === 1 ? '' : 's'}`;
    default:
      return decision.label;
  }
}

export function decisionCategoryLabel(category: string): string {
  return (
    {
      release_conflicts: 'Release or edition',
      album_completeness: 'Album completeness',
      pending_fulfillment: 'Waiting for tracks',
      apply_blockers: 'Saved actions need attention',
      numbering: 'Numbering',
      audio_conflicts: 'Audio assignment',
      title_issues: 'Recording identity',
      duplicates: 'Duplicate files',
      extra_files: 'Unmatched files',
      title_conventions: 'Title wording',
      metadata: 'Metadata',
    }[category] || category.replaceAll('_', ' ')
  );
}

export function numberingSchemeLabel(multipleDiscs: boolean, mode?: string): string {
  if (!multipleDiscs) return 'One disc · match release positions';
  return mode === 'digital' ? 'One continuous album sequence' : 'Numbered by release disc';
}
