import { describe, expect, it } from 'vitest';

import { ISSUE_CATEGORY_META } from './-issues.helpers';
import { issueSearchSchema } from './-issues.types';

describe('issueSearchSchema', () => {
  it('falls back to all for unknown categories', () => {
    expect(issueSearchSchema.parse({ category: 'not_real' })).toEqual({
      status: 'open',
      category: 'all',
      issueId: undefined,
    });
  });

  it('preserves known categories', () => {
    expect(issueSearchSchema.parse({ category: 'wrong_metadata' })).toEqual({
      status: 'open',
      category: 'wrong_metadata',
      issueId: undefined,
    });
  });

  it('falls back to open for unknown statuses', () => {
    expect(issueSearchSchema.parse({ status: 'not_real' })).toEqual({
      status: 'open',
      category: 'all',
      issueId: undefined,
    });
  });

  it('drops invalid issue ids', () => {
    expect(issueSearchSchema.parse({ issueId: 'abc123' })).toEqual({
      status: 'open',
      category: 'all',
      issueId: undefined,
    });
  });

  it('normalizes numeric issue ids', () => {
    expect(issueSearchSchema.parse({ issueId: '7' })).toEqual({
      status: 'open',
      category: 'all',
      issueId: 7,
    });
  });

  it('keeps the legacy category icons', () => {
    expect(ISSUE_CATEGORY_META.wrong_metadata.icon).toBe('✎');
    expect(ISSUE_CATEGORY_META.wrong_cover.icon).toBe('📷');
    expect(ISSUE_CATEGORY_META.audio_quality.icon).toBe('🎵');
  });
});
