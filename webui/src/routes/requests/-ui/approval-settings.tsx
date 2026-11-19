import { useEffect, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { Button, OptionButton, OptionButtonGroup } from '@/components/form';

import type { AcquisitionKind, AcquisitionMode, AcquisitionModes } from '../-requests.types';

import styles from './requests-page.module.css';

/** the four kinds a release can be picked for, in the order people think of them */
export const ACQUISITION_ROWS: ReadonlyArray<{
  kind: AcquisitionKind;
  label: string;
  hint: string;
}> = [
  { kind: 'music', label: 'Music', hint: 'Albums and tracks' },
  { kind: 'movie', label: 'Movies', hint: 'Every movie request' },
  { kind: 'tv', label: 'TV', hint: 'Shows and single episodes' },
  { kind: 'audiobook', label: 'Audiobooks', hint: 'Books, not podcast episodes' },
];

/**
 * Admin: how an approved request gets its release (#2199), one choice per
 * kind. Automatic is what approving always did. "I pick the release" holds
 * the approved request until someone picks one on the Pick a release tab.
 * Music videos, YouTube videos and podcast episodes have one file each, so
 * they always download right away.
 */
export function ApprovalSettingsDialog({
  open,
  modes,
  saving,
  onClose,
  onSave,
}: {
  open: boolean;
  modes: AcquisitionModes;
  saving: boolean;
  onClose: () => void;
  onSave: (changed: Partial<AcquisitionModes>) => void;
}) {
  const [draft, setDraft] = useState<AcquisitionModes>(modes);

  useEffect(() => {
    if (open) setDraft(modes);
  }, [open, modes]);

  const changed: Partial<AcquisitionModes> = {};
  for (const row of ACQUISITION_ROWS) {
    if (draft[row.kind] !== modes[row.kind]) changed[row.kind] = draft[row.kind];
  }
  const dirty = Object.keys(changed).length > 0;
  const set = (kind: AcquisitionKind, mode: AcquisitionMode) =>
    setDraft((d) => ({ ...d, [kind]: mode }));

  return (
    <DialogFrame open={open} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <DialogHeader title="When you approve a request">
        <span>What happens next, for each kind of request</span>
      </DialogHeader>
      <DialogBody>
        <ul className={styles.modeList}>
          {ACQUISITION_ROWS.map((row) => (
            <li key={row.kind} className={styles.modeRow}>
              <div className={styles.modeText}>
                <span className={styles.modeLabel}>{row.label}</span>
                <span className={styles.modeHint}>{row.hint}</span>
              </div>
              <OptionButtonGroup size="sm">
                <OptionButton
                  selected={draft[row.kind] === 'auto'}
                  aria-label={`${row.label}: download automatically`}
                  onClick={() => set(row.kind, 'auto')}
                >
                  Download automatically
                </OptionButton>
                <OptionButton
                  selected={draft[row.kind] === 'manual'}
                  aria-label={`${row.label}: I pick the release`}
                  onClick={() => set(row.kind, 'manual')}
                >
                  I pick the release
                </OptionButton>
              </OptionButtonGroup>
            </li>
          ))}
        </ul>
        <p className={styles.modeNote}>
          Picking it yourself: an approved request waits on the Pick a release tab, and nothing
          automatic downloads it until you choose one. You can still go the other way for one
          request from its menu when you approve it. Music videos and podcast episodes always
          download right away.
        </p>
      </DialogBody>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button variant="primary" disabled={!dirty || saving} onClick={() => onSave(changed)}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </DialogFooter>
    </DialogFrame>
  );
}
