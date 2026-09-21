import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { ListenbrainzImportControl } from './stats-page';

it('syncs with the account from Settings without asking for a username', () => {
  const run = vi.fn();
  render(
    <ListenbrainzImportControl
      onRun={run}
      running={false}
      status={{ success: true, token_configured: true, authenticated_user_available: true }}
    />,
  );
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByText('Account from Settings')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Sync ListenBrainz history' }));
  expect(run).toHaveBeenCalledTimes(1);
});

it('shows the saved account and disables sync when credentials are missing', () => {
  render(
    <ListenbrainzImportControl
      onRun={vi.fn()}
      running={false}
      status={{ success: true, username: 'saved-user', token_configured: false }}
    />,
  );
  expect(screen.getByText('saved-user')).toBeInTheDocument();
  expect(screen.getByText('Configure in Settings')).toBeInTheDocument();
  expect(screen.getByRole('button')).toBeDisabled();
});

it('prevents duplicate requests while starting and displays import progress', () => {
  const { rerender } = render(
    <ListenbrainzImportControl
      onRun={vi.fn()}
      running={true}
      status={{ success: true, username: 'saved-user', token_configured: true }}
    />,
  );
  expect(screen.getByRole('button', { name: 'Syncing ListenBrainz history' })).toBeDisabled();
  rerender(
    <ListenbrainzImportControl
      onRun={vi.fn()}
      running={false}
      status={{
        success: true,
        username: 'saved-user',
        token_configured: true,
        running: true,
        progress: 42,
      }}
    />,
  );
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '42');
  expect(screen.getByRole('button')).toBeDisabled();
});

it('offers a retry with the saved account after a failed sync', () => {
  const run = vi.fn();
  render(
    <ListenbrainzImportControl
      onRun={run}
      running={false}
      status={{
        success: true,
        username: 'saved-user',
        token_configured: true,
        status: 'error',
        error: 'HTTP 503',
      }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Sync failed');
  expect(screen.getByRole('status')).toHaveAttribute('title', 'HTTP 503');
  fireEvent.click(screen.getByRole('button', { name: 'Retry ListenBrainz history' }));
  expect(run).toHaveBeenCalledTimes(1);
});
