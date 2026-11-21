import { describe, expect, it } from 'vitest';

import { canRetryArt, fullArt, retryArt } from './images';

describe('library art', () => {
  it('a big view asks for the full picture', () => {
    expect(fullArt('/api/library/v2/artwork/album/5?size=thumb')).toBe(
      '/api/library/v2/artwork/album/5',
    );
    expect(fullArt('/base/api/library/v2/artwork/album/5?size=thumb&x=1')).toBe(
      '/base/api/library/v2/artwork/album/5?x=1',
    );
    expect(fullArt('https://img/x.jpg?size=thumb')).toBe('https://img/x.jpg?size=thumb');
    expect(fullArt(null)).toBeNull();
  });

  it('only library art is asked for again, past the browser remembering the miss', () => {
    expect(canRetryArt('/api/library/v2/artwork/album/5?size=thumb')).toBe(true);
    expect(canRetryArt('https://img/x.jpg')).toBe(false);
    expect(retryArt('/api/library/v2/artwork/album/5?size=thumb', 2)).toBe(
      '/api/library/v2/artwork/album/5?size=thumb&try=2',
    );
    expect(retryArt('/a/b', 1)).toBe('/a/b?try=1');
  });
});
