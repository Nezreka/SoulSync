import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useAccessibleModal } from './use-accessible-modal';

function Dialog({ onClose }: { onClose: () => void }) {
  const { ref } = useAccessibleModal<HTMLDivElement>(onClose);
  return (
    <div ref={ref} role="dialog" aria-modal="true" aria-label="d">
      <button type="button">inside</button>
    </div>
  );
}

describe('useAccessibleModal and popovers above it', () => {
  it('escape inside the dialog closes it', () => {
    const onClose = vi.fn();
    const { getByText } = render(<Dialog onClose={onClose} />);
    fireEvent.keyDown(getByText('inside'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('escape in a popover layer opened from it leaves the dialog open', () => {
    const onClose = vi.fn();
    const { getByText } = render(
      <>
        <Dialog onClose={onClose} />
        <div data-popover-layer="">
          <button type="button">in popover</button>
        </div>
      </>,
    );
    fireEvent.keyDown(getByText('in popover'), { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });
});
