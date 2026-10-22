/**
 * The prompts sit on DialogFrame, so Escape cancels them like the Cancel
 * button does, and the witness-me gate opens with its input focused.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { useFindingPrompts } from './finding-prompts';

function Harness({
  open,
}: {
  open: (prompts: ReturnType<typeof useFindingPrompts>) => Promise<unknown>;
}) {
  const prompts = useFindingPrompts();
  const [result, setResult] = useState('pending');
  return (
    <div>
      <button onClick={() => void open(prompts).then((value) => setResult(String(value)))}>
        open
      </button>
      <span data-testid="result">{result}</span>
      {prompts.promptNode}
    </div>
  );
}

describe('finding prompts', () => {
  it('Escape cancels an action prompt', async () => {
    render(<Harness open={(p) => p.promptOrphan()} />);
    fireEvent.click(screen.getByText('open'));
    expect(screen.getByRole('dialog', { name: 'Orphan File Action' })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.getByTestId('result').textContent).toBe('null'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('the witness-me gate focuses its input and Escape answers no', async () => {
    render(<Harness open={(p) => p.promptWitnessMe(5000)} />);
    fireEvent.click(screen.getByText('open'));
    const input = document.getElementById('witness-me-input');
    await waitFor(() => expect(document.activeElement).toBe(input));

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.getByTestId('result').textContent).toBe('false'));
  });
});
