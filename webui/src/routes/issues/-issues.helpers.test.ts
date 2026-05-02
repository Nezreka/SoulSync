import { describe, expect, it } from 'vite-plus/test';

import { normalizeIssuesSearch } from './-issues.helpers';

describe('normalizeIssuesSearch', () => {
  it('falls back to all for unknown categories', () => {
    expect(normalizeIssuesSearch({ category: 'not_real' })).toEqual({
      status: 'open',
      category: 'all',
    });
  });

  it('preserves known categories', () => {
    expect(normalizeIssuesSearch({ category: 'wrong_metadata' })).toEqual({
      status: 'open',
      category: 'wrong_metadata',
    });
  });

  it('drops invalid issue ids', () => {
    expect(normalizeIssuesSearch({ issueId: 'abc123' })).toEqual({
      status: 'open',
      category: 'all',
      issueId: undefined,
    });
  });

  it('normalizes numeric issue ids', () => {
    expect(normalizeIssuesSearch({ issueId: '7' })).toEqual({
      status: 'open',
      category: 'all',
      issueId: 7,
    });
  });
});
