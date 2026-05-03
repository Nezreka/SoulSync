import {
  type IssueCategory,
  type IssueRecord,
  type IssueSnapshot,
  type IssuePriority,
  type IssueStatus,
} from './-issues.types';

export const ISSUE_CATEGORY_META: Record<
  IssueCategory,
  { label: string; icon: string; description: string; applies: Array<'track' | 'album' | 'artist'> }
> = {
  wrong_track: {
    label: 'Wrong Track',
    icon: '❌',
    description: 'This file plays a different song than expected',
    applies: ['track'],
  },
  wrong_metadata: {
    label: 'Wrong Metadata',
    icon: '✎',
    description: 'Title, artist, year, or other tags are incorrect',
    applies: ['track', 'album'],
  },
  wrong_cover: {
    label: 'Wrong Cover Art',
    icon: '📷',
    description: 'The artwork is wrong or missing',
    applies: ['album'],
  },
  wrong_artist: {
    label: 'Wrong Artist',
    icon: '👤',
    description: 'This track is filed under the wrong artist',
    applies: ['track'],
  },
  duplicate_tracks: {
    label: 'Duplicate Tracks',
    icon: '🔁',
    description: 'The same track appears more than once in this album',
    applies: ['album'],
  },
  missing_tracks: {
    label: 'Missing Tracks',
    icon: '❓',
    description: 'Tracks that should be here are missing',
    applies: ['album'],
  },
  audio_quality: {
    label: 'Audio Quality',
    icon: '🎵',
    description: 'Audio has quality issues like clipping or low bitrate',
    applies: ['track'],
  },
  wrong_album: {
    label: 'Wrong Album',
    icon: '💿',
    description: 'This track belongs to a different album',
    applies: ['track'],
  },
  incomplete_album: {
    label: 'Incomplete Album',
    icon: '⚠',
    description: 'Album is partially downloaded',
    applies: ['album'],
  },
  other: {
    label: 'Other',
    icon: '💬',
    description: 'Any other issue not listed above',
    applies: ['track', 'album', 'artist'],
  },
};

export const ISSUE_STATUS_META: Record<IssueStatus, { label: string; className: string }> = {
  open: { label: 'Open', className: 'is-open' },
  in_progress: { label: 'In Progress', className: 'is-progress' },
  resolved: { label: 'Resolved', className: 'is-resolved' },
  dismissed: { label: 'Dismissed', className: 'is-dismissed' },
};

export function getIssueCategoriesForEntity(entityType: IssueRecord['entity_type']) {
  return Object.entries(ISSUE_CATEGORY_META).filter(([, category]) =>
    category.applies.includes(entityType),
  );
}

export function createDefaultIssueTitle(category: string, entityName: string): string {
  const label = getIssueCategoryMeta(category)?.label || 'Issue';
  return `${label}: ${entityName || 'Unknown'}`;
}

export function getIssueCategoryMeta(category: string) {
  return ISSUE_CATEGORY_META[category as IssueCategory];
}

export function getIssueStatusMeta(status: string) {
  return ISSUE_STATUS_META[status as IssueStatus];
}

export function parseSnapshot(snapshot: IssueRecord['snapshot_data']): IssueSnapshot {
  if (!snapshot) {
    return {};
  }
  if (typeof snapshot === 'string') {
    try {
      return JSON.parse(snapshot) as IssueSnapshot;
    } catch {
      return {};
    }
  }
  return snapshot;
}

export function getEntityLabel(entityType: IssueRecord['entity_type']): string {
  return entityType === 'track' ? 'Track' : entityType === 'album' ? 'Album' : 'Artist';
}

export function getEntityName(issue: IssueRecord, snapshot: IssueSnapshot): string {
  const entityLabel = getEntityLabel(issue.entity_type);
  return String(snapshot.title || snapshot.name || `${entityLabel} #${issue.entity_id}`);
}

export function getEntityDetails(issue: IssueRecord, snapshot: IssueSnapshot): string[] {
  const details: string[] = [];
  if (issue.entity_type === 'track') {
    if (snapshot.artist_name) details.push(String(snapshot.artist_name));
    if (snapshot.album_title) details.push(String(snapshot.album_title));
  } else if (issue.entity_type === 'album') {
    if (snapshot.artist_name) details.push(String(snapshot.artist_name));
  } else if (issue.entity_type === 'artist' && snapshot.name) {
    details.push(String(snapshot.name));
  }
  return details;
}

export function getIssueArtwork(snapshot: IssueSnapshot): string {
  return String(snapshot.thumb_url || snapshot.album_thumb || snapshot.artist_thumb || '');
}

export function formatIssueDate(value?: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatStatusLabel(status: string): string {
  return getIssueStatusMeta(status)?.label || status.replace(/_/g, ' ');
}

export function getPriorityClassName(priority: string): IssuePriority {
  if (priority === 'high') return 'high';
  if (priority === 'low') return 'low';
  return 'normal';
}
