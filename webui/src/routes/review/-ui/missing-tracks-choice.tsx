import { useState } from 'react';

import { Button, Checkbox } from '@/components/form/form';

import type { ReviewFinding } from '../-review.api';

import styles from './review-page.module.css';
import { record, records, textValue } from './review-presentation';

/** Requests are immediate wishlist actions; unselected tracks keep their album intent. */
export function MissingTracksChoice({
  findings,
  busy,
  onRequest,
  onChangeScope,
}: {
  findings: Pick<ReviewFinding, 'id' | 'details' | 'title'>[];
  busy: boolean;
  onRequest: (ids: number[]) => void;
  onChangeScope: () => void;
}) {
  const [selected, setSelected] = useState<number[]>([]);
  const available = findings.filter(
    (item) =>
      !['wishlisted', 'downloading', 'downloaded'].includes(
        textValue(record(item.details).fulfillment_state),
      ),
  );
  const ids = selected.filter((id) => available.some((item) => item.id === id));
  return (
    <section className={styles.missingChoices} aria-label="Choose missing tracks" tabIndex={-1}>
      <h4>Choose tracks to request</h4>
      <p>
        Selected tracks will be added to the wishlist. Other missing tracks remain wanted for this
        complete album.
      </p>
      <div className={styles.wantedTracks}>
        {available.map((item) => {
          const slots = records(record(item.details).missing_tracks);
          const title =
            slots
              .map((slot) => textValue(slot.name || slot.title))
              .filter(Boolean)
              .join(', ') || item.title;
          return (
            <label key={item.id}>
              <Checkbox
                disabled={busy}
                checked={ids.includes(item.id)}
                onCheckedChange={(checked) =>
                  setSelected((current) =>
                    checked ? [...current, item.id] : current.filter((id) => id !== item.id),
                  )
                }
              />
              <span>{title}</span>
            </label>
          );
        })}
      </div>
      <div className={styles.inlineActions}>
        <Button
          disabled={busy || !available.length}
          onClick={() =>
            setSelected(ids.length === available.length ? [] : available.map((item) => item.id))
          }
        >
          {ids.length === available.length && available.length ? 'Clear selection' : 'Select all'}
        </Button>
        <Button variant="primary" disabled={busy || !ids.length} onClick={() => onRequest(ids)}>
          Request selected tracks{ids.length ? ` (${ids.length})` : ''}
        </Button>
        <Button variant="ghost" disabled={busy} onClick={onChangeScope}>
          Change album completeness
        </Button>
      </div>
      {!available.length && <p>All missing tracks have already been requested.</p>}
    </section>
  );
}
