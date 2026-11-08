import { describe, expect, it } from 'vitest';

import { matchStatusText } from './library-v2-page';

describe('match chip wording (third #1062 review P2B-3)', () => {
  it('says what the workers last heard', () => {
    expect(matchStatusText('not_found')).toBe('not found');
    expect(matchStatusText('cleared')).toBe('cleared, match it by hand');
    expect(matchStatusText('error')).toBe('lookup failed, will retry');
    expect(matchStatusText('pending')).toBe('not tried yet');
    expect(matchStatusText('matched')).toBe('matched');
  });
});
