import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { FindingGroup } from '../-tools.groups';

import { FindingsInbox } from './findings-inbox';

// review 5 R5-L-3: under a severity filter a group row counts and colours the
// rows of that severity, not the whole status or the worst pending row
describe('findings inbox under a severity filter', () => {
  const metadataGap: FindingGroup = {
    finding_type: 'metadata_gap',
    pending: 301,
    resolved: 0,
    dismissed: 0,
    total: 301,
    severity_max: 'error',
    severities: { pending: { warning: 300, error: 1 } },
  };

  const renderInbox = (severity?: string) =>
    render(
      <FindingsInbox
        groups={[metadataGap]}
        typeInfo={() => undefined}
        status="pending"
        severity={severity}
        openType=""
        onToggleOpen={vi.fn()}
        onFixGroup={vi.fn()}
        onDismissGroup={vi.fn()}
        onReopenGroup={vi.fn()}
        busy={false}
      />,
    );

  it('counts the warnings, not every pending row', () => {
    const { container } = renderInbox('warning');
    expect(container.querySelector('.repair-inbox-count')?.textContent).toBe('300');
    expect(container.querySelector('.repair-inbox-sev')?.classList.contains('warning')).toBe(true);
  });

  it('with no severity filter it still shows the whole status', () => {
    const { container } = renderInbox();
    expect(container.querySelector('.repair-inbox-count')?.textContent).toBe('301');
    expect(container.querySelector('.repair-inbox-sev')?.classList.contains('error')).toBe(true);
  });
});
