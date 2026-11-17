/**
 * P4 replaces this file: Report a problem (plan 4.6), what happened, what's
 * included with a switch per line, the exact redacted preview, then GitHub,
 * Discord or a file. Admin only. Until then, the two places people go.
 */

import type { ViewProps } from './view-props';

import { DISCORD_URL, GITHUB_ISSUES_URL, openExternal } from './links';
import { Label, List, Placeholder, Row } from './ui';

export function ReportView(_: ViewProps) {
  return (
    <>
      <Placeholder
        title="Report a problem"
        text="Diagnostics you can read first, with secrets removed, are on the way. Meanwhile GitHub and Discord are where reports go."
      />
      <Label>Get help</Label>
      <List label="Get help">
        <Row
          icon="git"
          title="Open a GitHub issue"
          sub="Bugs and feature requests"
          end="tab"
          onClick={() => openExternal(GITHUB_ISSUES_URL)}
        />
        <Row
          icon="chat"
          title="Ask on Discord"
          sub="The SoulSync community"
          end="tab"
          onClick={() => openExternal(DISCORD_URL)}
        />
      </List>
    </>
  );
}
