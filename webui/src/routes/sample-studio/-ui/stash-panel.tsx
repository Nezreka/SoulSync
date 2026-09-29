import type { StashEntry } from '../-sample-studio.types';

import { stashExportUrl } from '../-sample-studio.api';
import { formatTime } from '../-sample-studio.helpers';
import { STASH_FORMAT_LABEL } from '../-sample-studio.types';
import styles from './sample-studio-page.module.css';

interface StashPanelProps {
  entries: StashEntry[] | undefined;
  isLoading: boolean;
  error: Error | null;
  onPlay: (entry: StashEntry) => void;
  onDelete: (entryId: number) => void;
}

function fxLabel(entry: StashEntry): string {
  const bits: string[] = [];
  if (Math.abs(entry.pitch_st) >= 0.01)
    bits.push(`${entry.pitch_st > 0 ? '+' : ''}${entry.pitch_st} st`);
  if (entry.target_bpm != null) bits.push(`→ ${entry.target_bpm} BPM`);
  return bits.join(' · ');
}

/** The personal sample stash: every saved chop (rendered file + bookmark). */
export function StashPanel({ entries, isLoading, error, onPlay, onDelete }: StashPanelProps) {
  return (
    <div className={styles.stashPanel}>
      <div className={styles.panelHeader}>
        <span>
          Sample Stash <span className={styles.resultCount}>{entries?.length ?? 0}</span>
        </span>
        <a className={styles.transportBtn} href={stashExportUrl()} download>
          ⬇ Export ZIP
        </a>
      </div>

      <div className={styles.scroll}>
        {isLoading && <div className={styles.statusLine}>Loading stash…</div>}
        {error && (
          <div className={styles.statusLine} data-tone="warn">
            Couldn&apos;t load the stash: {error.message}
          </div>
        )}

        {entries && entries.length === 0 && (
          <div className={styles.stashEmpty}>
            <strong>Nothing stashed yet</strong>
            Chop something in the editor, hit <b>Save chop</b>, and it lands here as a file plus a
            bookmark you can re-cut later.
          </div>
        )}

        {entries && entries.length > 0 && (
          <div className={styles.stashList}>
            {entries.map((entry) => (
              <div key={entry.id} className={styles.stashRow}>
                <button
                  type="button"
                  className={styles.stashPlay}
                  onClick={() => onPlay(entry)}
                  title={`Play ${entry.name}`}
                  aria-label={`Play ${entry.name}`}
                >
                  ▶
                </button>
                <div className={styles.stashMeta}>
                  <div className={styles.stashName}>{entry.name}</div>
                  <div className={styles.trackSub}>
                    {entry.track_title || 'Untitled'} · {entry.artist_name || 'Unknown artist'}
                  </div>
                  <div className={styles.trackSub}>
                    {formatTime(entry.start_s)}–{formatTime(entry.end_s)} ·{' '}
                    {STASH_FORMAT_LABEL[entry.format] ?? entry.format}
                    {fxLabel(entry) ? ` · ${fxLabel(entry)}` : ''}
                  </div>
                  {entry.tags.length > 0 && (
                    <div className={styles.tagRow}>
                      {entry.tags.map((t) => (
                        <span key={t} className={styles.tagChip}>
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  className={styles.transportBtn}
                  onClick={() => onDelete(entry.id)}
                  title={`Delete ${entry.name}`}
                >
                  🗑
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
