import { Button, TextInput } from '@/components/form/form';

import type { ReviewController } from './use-review-controller';

import { ReleaseComparison } from './reconciliation-evidence';
import styles from './review-page.module.css';
import { textValue } from './review-presentation';

type Props = Pick<
  ReviewController,
  | 'act'
  | 'active'
  | 'busy'
  | 'inspectReleaseId'
  | 'mutationBusy'
  | 'pinCandidate'
  | 'previewCandidate'
  | 'releaseCandidates'
  | 'releaseIdInput'
  | 'releaseLookupVersion'
  | 'reviewCase'
  | 'setManualRelease'
  | 'setPreviewCandidate'
  | 'setReleaseIdInput'
>;

export function AlbumRelease({
  act,
  active,
  busy,
  inspectReleaseId,
  mutationBusy,
  pinCandidate,
  previewCandidate,
  releaseCandidates,
  releaseIdInput,
  releaseLookupVersion,
  reviewCase,
  setManualRelease,
  setPreviewCandidate,
  setReleaseIdInput,
}: Props) {
  if (!reviewCase) return null;
  return (
    <section className={styles.evidenceView} aria-label="Release comparison">
      <div className={styles.sectionHeading}>
        <div>
          <div className={styles.eyebrow}>Release selection</div>
          <h3>Choose the edition for this album</h3>
          <p>
            The chosen release sets the track list. You can change it after reviewing individual
            tracks.
          </p>
        </div>
      </div>
      {Boolean(active?.details?.release_disambiguation) && (
        <p>Edition note: {textValue(active?.details?.release_disambiguation)}</p>
      )}
      <ReleaseComparison
        decisionReason={reviewCase.album_plan?.release_decision_reason}
        libraryRelease={reviewCase.album_plan?.current_release}
        candidates={releaseCandidates}
        view="list"
        selected={previewCandidate}
        onSelect={setPreviewCandidate}
        currentRelease={{
          source: reviewCase.album_plan?.source,
          release_id: reviewCase.album_plan?.release_id,
        }}
        numberingMode={reviewCase.album_plan?.numbering_mode}
        onPin={(index) =>
          !reviewCase.album_plan && active?.finding_type === 'reconciliation_release'
            ? void act(active, 'fix', String(index))
            : void pinCandidate(index)
        }
        busy={mutationBusy}
      />
      <div className={styles.manualRelease}>
        <h4>Have a different MusicBrainz edition?</h4>
        <p>Enter its release MBID or URL to inspect the track list before choosing it.</p>
        <div>
          <TextInput
            aria-label="MusicBrainz release MBID or URL"
            value={releaseIdInput}
            onChange={(event) => {
              releaseLookupVersion.current += 1;
              setReleaseIdInput(event.target.value);
              setManualRelease(null);
              setPreviewCandidate(0);
            }}
            placeholder="Release MBID or URL"
          />
          <Button
            type="button"
            className={styles.secondaryButton}
            disabled={busy}
            onClick={() => void inspectReleaseId()}
          >
            Load release
          </Button>
        </div>
      </div>
      {active?.finding_type === 'reconciliation_release' && (
        <Button type="button" disabled={busy} onClick={() => void act(active, 'dismiss')}>
          Keep current release
        </Button>
      )}
    </section>
  );
}
