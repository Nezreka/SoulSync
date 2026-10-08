import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { ProviderReleaseId } from './provider-release-id';

afterEach(cleanup);

describe('ProviderReleaseId', () => {
  it.each([
    ['musicbrainz', 'mb-release', 'https://musicbrainz.org/release/mb-release'],
    ['itunes', '1780804245', 'https://music.apple.com/us/album/1780804245'],
    ['deezer', '219614472', 'https://www.deezer.com/album/219614472'],
  ])('links a %s release to its public catalog page', (source, id, href) => {
    render(<ProviderReleaseId source={source} releaseId={id} />);
    expect(screen.getByRole('link', { name: id })).toHaveAttribute('href', href);
  });

  it('leaves an unknown provider as plain text', () => {
    render(<ProviderReleaseId source="local" releaseId="local-id" />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByText('local-id')).toBeTruthy();
  });
});
