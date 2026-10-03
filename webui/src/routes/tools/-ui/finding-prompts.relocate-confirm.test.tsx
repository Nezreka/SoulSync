/**
 * Relocate second confirmation (#1289 item 5).
 *
 * Relocate moves files out of the library into Staging — destructive enough
 * to deserve a second click. These tests pin the two-step behavior:
 * first click arms the confirmation, second click executes.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useFindingPrompts } from './finding-prompts';
import { useState } from 'react';

// Minimal harness to drive the prompt hook.
function Harness({ candidates, count }: { candidates?: string[]; count?: number }) {
  const prompts = useFindingPrompts();
  const [result, setResult] = useState<string | null | undefined>(undefined);
  return (
    <div>
      <button
        onClick={() => {
          void prompts.promptAcoustid(candidates, count).then(setResult);
        }}
      >
        open
      </button>
      <span data-testid="result">{String(result)}</span>
      {prompts.promptNode}
    </div>
  );
}

describe('relocate second confirmation', () => {
  it('first relocate click arms confirmation instead of resolving', () => {
    const { container } = render(<Harness />);
    fireEvent.click(screen.getByText('open'));

    // The AcoustID prompt should be visible with a Relocate button.
    const relocateBtn = screen.getByText('Relocate');
    fireEvent.click(relocateBtn);

    // Should NOT have resolved yet — instead shows the confirmation step.
    expect(screen.getByTestId('result').textContent).toBe('undefined');
    expect(screen.getByText('Confirm Relocate')).toBeTruthy();
    expect(container.querySelector('#_acid-relocate-confirm')).toBeTruthy();
  });

  it('second click on the confirm button resolves relocate', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByText('open'));
    fireEvent.click(screen.getByText('Relocate'));

    // Now click the armed confirmation button.
    fireEvent.click(screen.getByText('Yes, relocate it'));
    await waitFor(() => {
      expect(screen.getByTestId('result').textContent).toBe('relocate');
    });
  });

  it('back button disarms without resolving', () => {
    render(<Harness />);
    fireEvent.click(screen.getByText('open'));
    fireEvent.click(screen.getByText('Relocate'));
    fireEvent.click(screen.getByText('Back'));

    expect(screen.getByTestId('result').textContent).toBe('undefined');
    // Back on the first step — the plain Relocate button is back.
    expect(screen.getByText('Relocate')).toBeTruthy();
  });

  it('bulk count is reflected in the confirmation button and body', () => {
    const { container } = render(<Harness count={42} />);
    fireEvent.click(screen.getByText('open'));
    fireEvent.click(screen.getByText('Relocate'));

    // Button names the count…
    expect(screen.getByText('Yes, relocate 42 files')).toBeTruthy();
    // …and so does the body copy (not the singular "the file").
    const body = container.textContent || '';
    expect(body).toContain('42 files');
    expect(body).not.toContain('move the file out');
  });

  it('retag still acts immediately without a confirmation step', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByText('open'));
    fireEvent.click(screen.getByText('Retag'));

    await waitFor(() => {
      expect(screen.getByTestId('result').textContent).toBe('retag');
    });
  });
});
