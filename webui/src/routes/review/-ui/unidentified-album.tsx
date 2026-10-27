import { Button } from '@/components/form/form';

import type { ReviewCase } from '../-review.api';

import { ReviewAudio } from './review-audio';
import styles from './review-page.module.css';
import { record, records, textValue, libraryRelativePath } from './review-presentation';

export function UnidentifiedAlbum({
  reviewCase,
  busy,
  onIdentify,
  onLeave,
}: {
  reviewCase: ReviewCase;
  busy: boolean;
  onIdentify: () => void;
  onLeave?: () => void;
}) {
  const finding = reviewCase.items.find(
    (item) => item.finding_type === 'reconciliation_unresolved' && !item.file_path,
  );
  const files = reviewCase.album_plan?.assignments || [];
  const checked = records(finding?.details?.checked_files);
  const left = record(finding?.details?.planned_choice).action === 'dismiss';
  return (
    <section className={styles.unidentifiedAlbum} aria-label="Release not identified">
      <h3>Release not identified</h3>
      <p>
        {files.length || checked.length} local file
        {(files.length || checked.length) === 1 ? '' : 's'} found. No usable catalogue track list is
        available to compare them with.
      </p>
      <p>
        Inspect or listen to the files, then choose a MusicBrainz release. You can also leave this
        album as-is.
      </p>
      <div className={styles.decisionActions}>
        <Button onClick={onIdentify}>Identify release…</Button>
        {onLeave && !left && (
          <Button disabled={busy} onClick={onLeave}>
            Leave album as-is
          </Button>
        )}
        {left && <strong>Leave as-is planned</strong>}
      </div>
      <ul className={styles.unidentifiedFiles}>
        {(files.length
          ? files.map((file) => ({
              file: file.file || file.current_title,
              file_path: file.file_path,
              duration_ms: file.file_duration_ms,
              audio_status: file.acoustid?.status,
            }))
          : checked
        ).map((file, index) => (
          <li key={textValue(file.file_path) || index}>
            <strong>{textValue(file.file)}</strong>
            <span>
              {file.audio_status === 'no_match'
                ? 'Audio fingerprint has no identified recording'
                : 'Release assignment unavailable'}
            </span>
            {Boolean(file.file_path) && (
              <>
                <small>
                  {libraryRelativePath(
                    textValue(file.file_path),
                    reviewCase.album_plan?.library_root,
                  )}
                </small>
                <ReviewAudio
                  localPath={textValue(file.file_path)}
                  localTitle={textValue(file.file)}
                  localDurationMs={Number(file.duration_ms || 0)}
                />
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
