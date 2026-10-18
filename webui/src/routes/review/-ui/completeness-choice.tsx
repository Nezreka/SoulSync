import { useState } from 'react';

import { Button, Checkbox } from '@/components/form/form';

import type { ReviewFinding } from '../-review.api';

import styles from './review-page.module.css';
import { record, records, textValue } from './review-presentation';

function slotKey(slot: Record<string, unknown>): string {
  if (typeof slot.wanted_key === 'string' && slot.wanted_key) return slot.wanted_key;
  const id = textValue(slot.source_track_id).trim();
  if (id)
    return `id:${id}:${textValue(slot.disc_number || 1)}:${textValue(slot.track_number || 0)}`;
  const title = textValue(slot.title || slot.name)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return `position:${textValue(slot.disc_number || 1)}:${textValue(slot.track_number || 0)}:${title}`;
}

export function CompletenessChoice({
  finding,
  busy,
  onChoose,
}: {
  finding: ReviewFinding;
  busy: boolean;
  onChoose: (choice: string) => void;
}) {
  const missing = records(record(finding.details).missing_tracks);
  const [wanted, setWanted] = useState<string[]>([]);
  return (
    <div className={styles.completenessChoice}>
      <p>
        {missing.length} tracks are absent from this folder. Mark any you want to collect if this is
        a partial album.
      </p>
      <div className={styles.wantedTracks}>
        {missing.map((slot, index) => {
          const key = slotKey(slot);
          return (
            <label key={`${key}-${index}`}>
              <Checkbox
                checked={wanted.includes(key)}
                onCheckedChange={(checked) =>
                  setWanted((current) =>
                    checked ? [...current, key] : current.filter((value) => value !== key),
                  )
                }
              />
              <span>{textValue(slot.title || slot.name) || `Track ${index + 1}`}</span>
            </label>
          );
        })}
      </div>
      <div className={styles.decisionActions}>
        <Button type="button" disabled={busy} onClick={() => onChoose('complete')}>
          I want the complete album
        </Button>
        <Button
          type="button"
          disabled={busy}
          onClick={() => onChoose(`partial:${JSON.stringify(wanted)}`)}
        >
          Keep as partial{wanted.length ? ` · ${wanted.length} wanted` : ''}
        </Button>
      </div>
    </div>
  );
}
