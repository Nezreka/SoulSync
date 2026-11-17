/**
 * P6 replaces this file: What's new (plan 4.7), the release's items newest
 * first with Show me, from release-notes.ts. Until then, the full notes
 * behind the version button.
 */

import type { ViewProps } from './view-props';

import { openReleaseNotes } from './links';
import { useHelpStore } from './store';
import { Button, Placeholder } from './ui';

export function WhatsNewView(_: ViewProps) {
  const version = useHelpStore((st) => st.update?.current_version);
  return (
    <Placeholder
      title={version ? `SoulSync ${version}` : "What's new"}
      text="What changed in this release, with a way to see each thing, shows here soon."
    >
      <Button kind="primary" icon="spark" onClick={openReleaseNotes}>
        Full release notes
      </Button>
    </Placeholder>
  );
}
