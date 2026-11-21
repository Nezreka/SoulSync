import { render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { groupFingerprint, ServerFingerprint } from './server-fingerprint';

afterEach(() => vi.unstubAllGlobals());

function stub(body: unknown, status = 200) {
  const fn = vi.fn(() =>
    Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) } as Response),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('ServerFingerprint', () => {
  it('groups the 12 characters in fours', () => {
    expect(groupFingerprint('ae0396a668c3')).toBe('ae03 96a6 68c3');
  });

  it('shows the fingerprint from the server', async () => {
    stub({
      success: true,
      fingerprint: 'ae0396a668c3',
      lookup_id: 'ae0396a668c3' + 'f'.repeat(52),
    });
    const { container } = render(<ServerFingerprint />);
    await waitFor(() =>
      expect(container.querySelector('code')?.textContent).toBe('ae03 96a6 68c3'),
    );
  });

  it('says why when the server cannot read it', async () => {
    stub({ success: false, error: 'The server identity could not be read.' }, 500);
    const { getByText } = render(<ServerFingerprint />);
    await waitFor(() => getByText('The server identity could not be read.'));
  });

  it('asks nothing until its tab has been opened', () => {
    const fn = stub({ success: true });
    render(<ServerFingerprint load={false} />);
    expect(fn).not.toHaveBeenCalled();
  });
});
