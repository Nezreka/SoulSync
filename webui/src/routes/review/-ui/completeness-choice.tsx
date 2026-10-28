import { useState } from 'react';

import { Button, Checkbox, TextInput } from '@/components/form/form';

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
  return (
    <CompletenessOptions
      key={JSON.stringify([
        finding.id,
        finding.details?.source,
        finding.details?.release_id,
        missing.map(slotKey),
      ])}
      missing={missing}
      selected={
        Array.isArray(finding.details?.wanted_slots) ? finding.details.wanted_slots.map(String) : []
      }
      busy={busy}
      onChoose={onChoose}
    />
  );
}

function CompletenessOptions({
  missing,
  selected,
  busy,
  onChoose,
}: {
  missing: Record<string, unknown>[];
  selected: string[];
  busy: boolean;
  onChoose: (choice: string) => void;
}) {
  const [wanted, setWanted] = useState<string[]>(() =>
    missing
      .filter((slot) => slot.requested === true || selected.includes(slotKey(slot)))
      .map(slotKey),
  );
  const [query, setQuery] = useState('');
  const large = missing.length > 12;
  const shown = missing.filter((slot) =>
    textValue(slot.title || slot.name)
      .toLocaleLowerCase()
      .includes(query.toLocaleLowerCase()),
  );
  const discs = [...new Set(shown.map((slot) => Number(slot.disc_number || 1)))].sort(
    (a, b) => a - b,
  );
  const multipleDiscs = new Set(missing.map((slot) => Number(slot.disc_number || 1))).size > 1;
  const choices = (slots: Record<string, unknown>[]) => (
    <div className={styles.wantedTracks}>
      {slots.map((slot, index) => {
        const key = slotKey(slot);
        return (
          <label key={`${key}-${index}`}>
            <Checkbox
              disabled={busy || slot.requested === true}
              checked={wanted.includes(key)}
              onCheckedChange={(checked) =>
                setWanted((current) =>
                  checked ? [...current, key] : current.filter((value) => value !== key),
                )
              }
            />
            <span>
              {textValue(slot.title || slot.name) || `Track ${index + 1}`}
              {slot.requested === true && <small> · Already requested</small>}
            </span>
          </label>
        );
      })}
    </div>
  );
  return (
    <div className={styles.completenessChoice}>
      <p>
        {missing.length} {missing.length === 1 ? 'track is' : 'tracks are'} absent from this folder.
        Mark any you want to collect if this is a partial album.
      </p>
      {large ? (
        <details className={styles.missingDisclosure}>
          <summary>
            {missing.length} missing tracks · {wanted.length} selected
          </summary>
          <TextInput
            aria-label="Find missing tracks"
            placeholder="Find a missing track…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className={styles.missingScroll}>
            {!shown.length && <p>No matching tracks.</p>}
            {discs.map((disc) => (
              <section key={disc} className={styles.missingDisc}>
                {multipleDiscs && <h5>Disc {disc}</h5>}
                {choices(shown.filter((slot) => Number(slot.disc_number || 1) === disc))}
              </section>
            ))}
          </div>
        </details>
      ) : (
        choices(missing)
      )}
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
