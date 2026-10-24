import { useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { Button } from '@/components/form/form';
import { HelpHint } from '@/components/release-metadata/release-metadata';

import type { AlbumAssignment } from '../-review.api';

import styles from './review-page.module.css';
import { formatTrackPosition } from './review-position';

export function NumberingChoice({
  assignments,
  scheme,
  busy,
  onChoose,
}: {
  assignments: AlbumAssignment[];
  scheme: string;
  busy: boolean;
  onChoose: (choice: 'fix' | 'dismiss') => void;
}) {
  const [open, setOpen] = useState(false);
  const examples = assignments
    .filter(
      (row) =>
        row.file_path &&
        (row.current_track_number !== row.slot_track_number ||
          row.current_disc_number !== row.slot_disc_number),
    )
    .slice(0, 3);
  const choose = (choice: 'fix' | 'dismiss') => {
    setOpen(false);
    onChoose(choice);
  };
  return (
    <>
      <Button type="button" disabled={busy} onClick={() => setOpen(true)}>
        Change…
      </Button>
      <DialogFrame open={open} onOpenChange={setOpen}>
        <DialogHeader title="Album numbering">{scheme}</DialogHeader>
        <DialogBody>
          <p>
            Choose how disc and track numbers will be written across this album.
            <HelpHint label="album numbering">
              Release numbering uses the selected edition’s disc and track positions. Keeping
              current numbering preserves the numbers in your files, including continuous numbering
              across discs.
            </HelpHint>
          </p>
          {examples.length > 0 && (
            <table className={styles.numberingExamples}>
              <thead>
                <tr>
                  <th>Track</th>
                  <th>Current</th>
                  <th>Release</th>
                </tr>
              </thead>
              <tbody>
                {examples.map((row) => (
                  <tr key={row.file_path}>
                    <td>{row.current_title || row.slot_title}</td>
                    <td>
                      {formatTrackPosition(row.current_disc_number, row.current_track_number)}
                    </td>
                    <td>{formatTrackPosition(row.slot_disc_number, row.slot_track_number)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </DialogBody>
        <DialogFooter>
          <Button type="button" disabled={busy} onClick={() => choose('dismiss')}>
            Keep current numbering
          </Button>
          <Button type="button" variant="primary" disabled={busy} onClick={() => choose('fix')}>
            Use release numbering
          </Button>
        </DialogFooter>
      </DialogFrame>
    </>
  );
}
