/**
 * P4 replaces this file: Health (plan 4.5), the full findings from
 * GET /api/help/health with a fix for each, Needs you / Worth a look /
 * Running fine, and Check again. Admin only: the store never pushes it for
 * a member. Until then it reads the status the button's dot reads.
 */

import type { HelpStatus } from './api';
import type { ViewProps } from './view-props';

import { openSettingsTab } from './links';
import { useHelpStore } from './store';
import { Button, Label, List, Placeholder, Row } from './ui';

export function HealthView(_: ViewProps) {
  const status = useHelpStore((st) => st.status);
  const top = status?.top ?? [];
  return (
    <>
      <Placeholder
        title={
          !status
            ? 'Checking SoulSync'
            : status.errors
              ? `${status.errors} ${status.errors === 1 ? 'thing needs' : 'things need'} you`
              : 'Everything is running'
        }
        text="Every check with its fix shows here soon. The live log has the detail meanwhile."
      >
        <Button icon="terminal" onClick={() => openSettingsTab('logs')}>
          Open the logs
        </Button>
      </Placeholder>
      <Findings label="Needs you" items={top.filter((f) => f.severity === 'error')} />
      <Findings label="Worth a look" items={top.filter((f) => f.severity === 'warning')} />
    </>
  );
}

function Findings({ label, items }: { label: string; items: HelpStatus['top'] }) {
  if (!items.length) return null;
  return (
    <>
      <Label>{label}</Label>
      <List label={label}>
        {items.map((f) => (
          <Row
            key={f.id}
            icon="alert"
            title={f.title}
            sub={f.severity === 'error' ? 'Error' : 'Warning'}
          />
        ))}
      </List>
    </>
  );
}
