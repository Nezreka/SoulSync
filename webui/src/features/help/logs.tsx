/**
 * P4 replaces this file: Logs (plan 9.1), every log SoulSync writes with its
 * backups, download one or all as a zip, secrets removed by default. Admin
 * only. Until then, the live log in Settings.
 */

import type { ViewProps } from './view-props';

import { openSettingsTab } from './links';
import { Button, Placeholder } from './ui';

export function LogsView(_: ViewProps) {
  return (
    <Placeholder
      title="Server logs"
      text="Downloading every log, with secrets removed, comes here soon. The live log is in Settings."
    >
      <Button kind="primary" icon="terminal" onClick={() => openSettingsTab('logs')}>
        Open the live log
      </Button>
    </Placeholder>
  );
}
