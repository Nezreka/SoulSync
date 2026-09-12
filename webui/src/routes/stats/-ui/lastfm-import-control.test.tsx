import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';

import { LastfmImportControl } from './stats-page';

it('allows correcting a saved typo after a failed import and submits the corrected username', () => {
  const run = vi.fn();
  function Harness() {
    const [username, setUsername] = useState('k');
    return (
      <LastfmImportControl
        username={username}
        onUsernameChange={setUsername}
        onRun={() => run(username)}
        running={false}
        status={{ success: true, username: 'k', status: 'error', api_key_configured: true }}
      />
    );
  }
  render(<Harness />);
  const input = screen.getByRole('textbox', { name: 'Last.fm username' });
  fireEvent.change(input, { target: { value: '' } });
  expect(input).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled();
  fireEvent.change(input, { target: { value: 'c' } });
  expect(screen.getByRole('textbox', { name: 'Last.fm username' })).toBe(input);
  fireEvent.change(input, { target: { value: 'corrected' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run' }));
  expect(run).toHaveBeenCalledWith('corrected');
});
