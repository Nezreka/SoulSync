import { Button } from '@/components/form/form';
import { ProviderReleaseId } from '@/components/provider-reference/provider-reference';
import { HelpHint, ReleaseBadges } from '@/components/release-metadata/release-metadata';

import type { ReviewController } from './use-review-controller';

import { type ReviewDecision } from '../-review.api';
import { NumberingChoice } from './numbering-choice';
import { ReleaseComparison } from './reconciliation-evidence';
import { numberingSchemeLabel } from './review-labels';
import styles from './review-page.module.css';
import { NUMBERING_FIELDS } from './review-position';
import { libraryRelativePath } from './review-presentation';
import { record, textValue } from './review-presentation';

type Props = Pick<
  ReviewController,
  | 'actGroup'
  | 'albumReady'
  | 'albumTitleChange'
  | 'albumView'
  | 'assignments'
  | 'caseId'
  | 'hasIntentDecision'
  | 'mappedSlots'
  | 'mutationBusy'
  | 'plan'
  | 'previewCandidate'
  | 'primaryCandidate'
  | 'proposedChangeGroups'
  | 'reconsiderScope'
  | 'releaseCandidates'
  | 'releaseChoicePending'
  | 'releaseHasMultipleDiscs'
  | 'reviewCase'
  | 'reviewDecisions'
  | 'setAlbumView'
  | 'setPreviewCandidate'
  | 'settledSlots'
  | 'showTrackList'
  | 'sourceFolders'
  | 'totalSlots'
  | 'unidentified'
  | 'wishlistedCount'
>;

export function AlbumContext({
  actGroup,
  albumReady,
  albumTitleChange,
  albumView,
  assignments,
  caseId,
  hasIntentDecision,
  mappedSlots,
  mutationBusy,
  plan,
  previewCandidate,
  primaryCandidate,
  proposedChangeGroups,
  reconsiderScope,
  releaseCandidates,
  releaseChoicePending,
  releaseHasMultipleDiscs,
  reviewCase,
  reviewDecisions,
  setAlbumView,
  setPreviewCandidate,
  settledSlots,
  showTrackList,
  sourceFolders,
  totalSlots,
  unidentified,
  wishlistedCount,
}: Props) {
  if (!reviewCase) return null;
  return (
    <aside className={styles.tabContext} aria-label={`${albumView} context`}>
      {albumView === 'plan' && !unidentified && reviewCase.album_plan && (
        <section className={styles.planContext} aria-label="Album plan context">
          <div>
            <div className={styles.eyebrow}>Album context</div>
            <h4>What this decision affects</h4>
          </div>
          <dl>
            <div>
              <dt>Selected release</dt>
              <dd>
                {primaryCandidate?.release_title || 'Release title unavailable'}
                {primaryCandidate?.release_artist && (
                  <span className={styles.releaseArtist}>{primaryCandidate.release_artist}</span>
                )}
                {primaryCandidate && (
                  <ReleaseBadges
                    format={primaryCandidate.format}
                    country={primaryCandidate.country}
                    disambiguation={primaryCandidate.disambiguation}
                  />
                )}
                <div className={styles.releaseReference}>
                  <ProviderReleaseId
                    source={reviewCase.album_plan.source}
                    releaseId={reviewCase.album_plan.release_id}
                  />
                </div>
              </dd>
            </div>
            <div>
              <dt>
                Track coverage
                <HelpHint label="track coverage">
                  Assigned slots have a proposed local file. A match can still need review; coverage
                  does not mean that every file is verified or will be changed.
                </HelpHint>
              </dt>
              <dd>
                {mappedSlots} of {totalSlots || '?'} release slots assigned
              </dd>
            </div>
            <div>
              <dt>Album scope</dt>
              <dd>
                {reviewCase.album_plan.intent_inferred
                  ? 'One playlist track owned; other tracks are uncollected'
                  : reviewCase.album_plan.intent_scope ||
                    (hasIntentDecision
                      ? 'Choice pending'
                      : reviewCase.album_plan.counts.missing
                        ? albumReady
                          ? 'Missing tracks have planned resolutions'
                          : 'Resolve track assignments first'
                        : 'Full release coverage')}
              </dd>
            </div>
            {albumTitleChange && (
              <div>
                <dt id="review-shared-album-title">Shared album title</dt>
                <dd>
                  <span className={styles.muted}>{textValue(albumTitleChange.old)}</span>
                  {' → '}
                  <strong>{textValue(albumTitleChange.new)}</strong>
                  <p className={styles.muted}>
                    Updates the album’s shared library title once.
                    {assignments
                      .filter((row) => row.file_path)
                      .every(
                        (row) =>
                          textValue(row.current_tags?.album) === textValue(albumTitleChange.new),
                      )
                      ? ' The file album tags already use this title.'
                      : ' Changes to individual file album tags are listed with their tracks.'}
                  </p>
                </dd>
              </div>
            )}
            {wishlistedCount > 0 && (
              <div>
                <dt>Acquisition</dt>
                <dd>
                  {wishlistedCount} track{wishlistedCount === 1 ? '' : 's'} requested; awaiting
                  files
                </dd>
              </div>
            )}
            <div>
              <dt>Already settled</dt>
              <dd>
                {settledSlots === 0
                  ? 'None yet'
                  : `${settledSlots} track${settledSlots === 1 ? '' : 's'} need${
                      settledSlots === 1 ? 's' : ''
                    } no decision`}
              </dd>
            </div>
            <div>
              <dt>Planned numbering</dt>
              <dd className={styles.numberingSummary}>
                {numberingSchemeLabel(
                  releaseHasMultipleDiscs,
                  reviewCase.album_plan.numbering_mode,
                )}
                {!reviewDecisions.some((decision) => decision.category === 'numbering') &&
                  reviewCase.items.some(
                    (item) =>
                      item.details?.routine_numbering &&
                      NUMBERING_FIELDS.some((field) => field in record(item.details?.changes)),
                  ) && (
                    <NumberingChoice
                      key={caseId}
                      assignments={assignments}
                      scheme={numberingSchemeLabel(
                        releaseHasMultipleDiscs,
                        reviewCase.album_plan.numbering_mode,
                      )}
                      busy={mutationBusy || releaseChoicePending}
                      onChoose={(choice) =>
                        void actGroup({ category: 'numbering' } as ReviewDecision, choice)
                      }
                    />
                  )}
              </dd>
            </div>
          </dl>
          {sourceFolders.length > 1 && (
            <section className={styles.folderContext} aria-label="Combined album folders">
              <h4>{sourceFolders.length} folders reviewed together</h4>
              <p>
                Track coverage and duplicate checks include all these files. Folder organization is
                a separate action.
              </p>
              <ul>
                {sourceFolders.map((folder) => (
                  <li key={folder}>
                    <code title={folder}>
                      {libraryRelativePath(folder, reviewCase.album_plan?.library_root)}
                    </code>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <div className={`${styles.actions} ${styles.planContextActions}`}>
            <Button type="button" onClick={() => showTrackList()}>
              Review track assignments
            </Button>
            {reviewCase.album_plan.intent_scope && (
              <Button type="button" disabled={mutationBusy} onClick={() => void reconsiderScope()}>
                {reviewCase.album_plan.intent_inferred
                  ? 'Choose tracks to collect'
                  : 'Review album scope'}
              </Button>
            )}
          </div>
        </section>
      )}

      {albumView === 'plan' &&
        !albumReady &&
        plan.status !== 'waiting' &&
        proposedChangeGroups.length > 0 && (
          <section className={styles.planContext} aria-label="Proposed metadata by field">
            <div>
              <div className={styles.eyebrow}>Proposed metadata</div>
              <h4>Changes by field</h4>
              <p>Choose a field to see the affected tracks and exact values.</p>
            </div>
            <div className={styles.readySummaryList}>
              {proposedChangeGroups.map((group) => (
                <Button
                  key={group.key}
                  type="button"
                  onClick={() => {
                    if (group.key === 'library_album_title') setAlbumView('plan');
                    else showTrackList(group.key);
                  }}
                >
                  <span>
                    <strong>{group.label}</strong>
                    <small>{group.valueSummary}</small>
                  </span>
                  <b>
                    {group.key === 'library_album_title'
                      ? 'Whole album'
                      : `${group.count} track${group.count === 1 ? '' : 's'}`}{' '}
                    →
                  </b>
                </Button>
              ))}
            </div>
          </section>
        )}

      {albumView === 'release' && (
        <ReleaseComparison
          decisionReason={reviewCase.album_plan?.release_decision_reason}
          candidates={releaseCandidates}
          selected={previewCandidate}
          onSelect={setPreviewCandidate}
          view="detail"
          numberingMode={reviewCase.album_plan?.numbering_mode}
        />
      )}
    </aside>
  );
}
