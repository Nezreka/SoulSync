import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { LastfmImportControl } from './stats-page';

it('syncs with the account from Settings without asking for a username', () => {
  const run = vi.fn();
  render(
    <LastfmImportControl
      onRun={run}
      running={false}
      status={{ success: true, api_key_configured: true, authenticated_user_available: true }}
    />,
  );
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByText('Account from Settings')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Sync Last.fm history' }));
  expect(run).toHaveBeenCalledTimes(1);
});

it('shows the saved account and disables sync when credentials are missing', () => {
  render(
    <LastfmImportControl
      onRun={vi.fn()}
      running={false}
      status={{ success: true, username: 'saved-user', api_key_configured: false }}
    />,
  );
  expect(screen.getByText('saved-user')).toBeInTheDocument();
  expect(screen.getByText('Configure in Settings')).toBeInTheDocument();
  expect(screen.getByRole('button')).toBeDisabled();
});

it('prevents duplicate requests while starting and displays import progress', () => {
  const { rerender } = render(
    <LastfmImportControl
      onRun={vi.fn()}
      running={true}
      status={{ success: true, username: 'saved-user', api_key_configured: true }}
    />,
  );
  expect(screen.getByRole('button', { name: 'Syncing Last.fm history' })).toBeDisabled();
  rerender(
    <LastfmImportControl
      onRun={vi.fn()}
      running={false}
      status={{
        success: true,
        username: 'saved-user',
        api_key_configured: true,
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
    <LastfmImportControl
      onRun={run}
      running={false}
      status={{
        success: true,
        username: 'saved-user',
        api_key_configured: true,
        status: 'error',
        error: 'HTTP 503',
      }}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Sync failed');
  expect(screen.getByRole('status')).toHaveAttribute('title', 'HTTP 503');
  fireEvent.click(screen.getByRole('button', { name: 'Retry Last.fm history' }));
  expect(run).toHaveBeenCalledTimes(1);
});
