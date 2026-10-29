import { Fragment } from 'react';

import { Button } from '@/components/form/form';

import type { ReviewController } from './use-review-controller';

import { CompletenessChoice } from './completeness-choice';
import { TrackRequests } from './file-decisions';
import { decisionInstruction, decisionCategoryLabel } from './review-labels';
import styles from './review-page.module.css';

type Props = Pick<
  ReviewController,
  | 'act'
  | 'actGroup'
  | 'active'
  | 'activeAssignment'
  | 'applyingThisAlbum'
  | 'blockedActionCount'
  | 'busy'
  | 'busyDecision'
  | 'choiceCount'
  | 'mutationBusy'
  | 'openDecision'
  | 'plan'
  | 'releaseChoicePending'
  | 'releaseHasMultipleDiscs'
  | 'requestAllMissing'
  | 'reviewCase'
  | 'reviewDecisions'
  | 'selectedActionDetail'
  | 'setAlbumView'
  | 'showTrackList'
>;

export function AlbumDecisions({
  act,
  actGroup,
  active,
  activeAssignment,
  applyingThisAlbum,
  blockedActionCount,
  busy,
  busyDecision,
  choiceCount,
  mutationBusy,
  openDecision,
  plan,
  releaseChoicePending,
  releaseHasMultipleDiscs,
  requestAllMissing,
  reviewCase,
  reviewDecisions,
  selectedActionDetail,
  setAlbumView,
  showTrackList,
}: Props) {
  if (!reviewCase) return null;
  return (
    <section className={styles.decisionBoard} aria-label="Album decisions">
      <div className={styles.sectionHeading}>
        <div>
          <div className={styles.eyebrow}>Album review</div>
          <h3>
            {blockedActionCount && !choiceCount
              ? `${blockedActionCount} saved action${blockedActionCount === 1 ? '' : 's'} need${blockedActionCount === 1 ? 's' : ''} attention`
              : reviewDecisions.length
                ? `${choiceCount} choice${choiceCount === 1 ? '' : 's'} need${choiceCount === 1 ? 's' : ''} your input${blockedActionCount ? ` · ${blockedActionCount} blocked action${blockedActionCount === 1 ? '' : 's'}` : ''}`
                : plan.status === 'waiting'
                  ? 'Waiting for requested tracks'
                  : 'Decisions complete'}
          </h3>
          <p>
            {blockedActionCount && !choiceCount
              ? 'Your choices are saved. Resolve the apply issues below before retrying this album plan.'
              : releaseChoicePending
                ? 'Choose the release first. Track and numbering proposals will be recalculated for that edition.'
                : plan.status === 'waiting'
                  ? 'Your requests are saved. The album stays here until the downloaded files are assigned. Other approved changes can be applied now.'
                  : reviewDecisions.length
                    ? 'Resolve these choices before applying the album. Inspect affected tracks to compare exact values.'
                    : 'All decisions are complete. There are no remaining changes to apply.'}
          </p>
        </div>
      </div>
      <div className={styles.decisionGrid}>
        {reviewDecisions.map((decision, decisionIndex) => {
          const instruction = decisionInstruction(
            decision,
            reviewCase.album_plan?.numbering_mode,
            releaseHasMultipleDiscs,
          );
          const hasGroupActions = ['numbering', 'title_conventions', 'metadata'].includes(
            decision.category,
          );
          const selected = active ? decision.finding_ids.includes(active.id) : false;
          return (
            <Fragment key={decision.id}>
              <article
                data-risk={decision.risk}
                data-state={
                  releaseChoicePending && decision.category !== 'release_conflicts'
                    ? 'pending'
                    : 'needs-review'
                }
                data-selected={selected || undefined}
              >
                <Button
                  type="button"
                  className={styles.decisionSelect}
                  onClick={() => {
                    if (hasGroupActions) {
                      showTrackList(`decision:${decision.id}`);
                    } else if (decision.category === 'release_conflicts') {
                      setAlbumView('release');
                    } else {
                      openDecision(decision);
                    }
                  }}
                >
                  <span className={styles.decisionStep} aria-hidden="true">
                    {decisionIndex + 1}
                  </span>
                  <span>
                    <small>{decisionCategoryLabel(decision.category)}</small>
                    <strong>{instruction}</strong>
                    {decision.summary && decision.summary !== instruction && (
                      <small>{decision.summary}</small>
                    )}
                    {decision.additional_metadata_summary && (
                      <small>{decision.additional_metadata_summary}</small>
                    )}
                    {hasGroupActions && <small>Inspect affected tracks →</small>}
                  </span>
                  {reviewDecisions.length > 1 && (
                    <b>
                      {decision.action_count} action
                      {decision.action_count === 1 ? '' : 's'}
                      {' →'}
                    </b>
                  )}
                </Button>
                {decision.category === 'audio_conflicts' &&
                  decision.finding_ids.length > 0 &&
                  decision.finding_ids.every((id) =>
                    reviewCase.items.some(
                      (item) => item.id === id && item.details?.catalogue_disagreement,
                    ),
                  ) && (
                    <div className={styles.decisionActions}>
                      <p>
                        File tags and the selected release agree. AcoustID credits these recordings
                        differently. Inspect the tracks, or confirm these proposed assignments
                        together.
                      </p>
                      <Button
                        type="button"
                        variant="primary"
                        disabled={mutationBusy || releaseChoicePending}
                        onClick={() =>
                          void actGroup({ ...decision, category: 'catalogue_assignments' }, 'fix')
                        }
                      >
                        Use these {decision.finding_ids.length} assignments
                      </Button>
                    </div>
                  )}
                {hasGroupActions && (
                  <div className={styles.decisionActions}>
                    <Button
                      type="button"
                      disabled={
                        busy ||
                        applyingThisAlbum ||
                        releaseChoicePending ||
                        reviewCase.refresh?.state === 'running'
                      }
                      variant="primary"
                      onClick={() => void actGroup(decision, 'fix')}
                    >
                      {busyDecision === decision.category
                        ? `Saving ${decision.action_count} choices…`
                        : decision.additional_metadata_summary && decision.category !== 'numbering'
                          ? `Plan title changes and album metadata for ${decision.action_count} track${decision.action_count === 1 ? '' : 's'}`
                          : decision.category === 'numbering'
                            ? !releaseHasMultipleDiscs
                              ? `Plan release track numbers for ${decision.action_count} track${decision.action_count === 1 ? '' : 's'}`
                              : `Plan release numbering for ${decision.action_count} track${decision.action_count === 1 ? '' : 's'}`
                            : decision.category === 'title_conventions'
                              ? 'Use proposed title conventions'
                              : decision.label === 'Confirm album artist'
                                ? 'Use proposed album artist'
                                : `Use ${decision.action_count} proposed changes`}
                    </Button>
                    <Button
                      type="button"
                      disabled={
                        busy ||
                        applyingThisAlbum ||
                        releaseChoicePending ||
                        reviewCase.refresh?.state === 'running'
                      }
                      onClick={() => void actGroup(decision, 'dismiss')}
                    >
                      {decision.category === 'numbering'
                        ? 'Keep current numbering'
                        : decision.label === 'Confirm album artist'
                          ? 'Keep current album artist'
                          : 'Keep current values'}
                    </Button>
                  </div>
                )}
                {busyDecision === decision.category && (
                  <p className={styles.decisionProgress} role="status">
                    <span className={styles.loadingSpinner} aria-hidden="true" />
                    Saving your choices in the album plan…
                  </p>
                )}
                {decision.category === 'album_completeness' &&
                  (() => {
                    const intent = reviewCase.items.find(
                      (item) =>
                        item.finding_type === 'reconciliation_intent' &&
                        decision.finding_ids.includes(item.id),
                    );
                    return intent ? (
                      <CompletenessChoice
                        key={intent.id}
                        finding={intent}
                        busy={mutationBusy}
                        onChoose={(choice) => void act(intent, 'fix', choice)}
                      />
                    ) : (
                      <div className={styles.requestStep}>
                        <TrackRequests
                          items={reviewCase.items.filter((item) =>
                            decision.finding_ids.includes(item.id),
                          )}
                        />
                        <div className={styles.decisionActions}>
                          <Button
                            type="button"
                            disabled={
                              applyingThisAlbum || busy || reviewCase.refresh?.state === 'running'
                            }
                            onClick={() => void requestAllMissing(decision.action_count)}
                          >
                            Request{' '}
                            {decision.action_count === 1
                              ? 'the missing track'
                              : `all ${decision.action_count} missing tracks`}
                          </Button>
                          <Button
                            onClick={() => {
                              showTrackList('missing');
                            }}
                          >
                            Choose individual tracks →
                          </Button>
                        </div>
                        <small>
                          Each requested track is added to the wishlist for the selected release.
                        </small>
                      </div>
                    );
                  })()}
              </article>
              {selected &&
                !activeAssignment &&
                active?.finding_type !== 'reconciliation_intent' &&
                active?.finding_type !== 'reconciliation_release' &&
                !['numbering', 'title_conventions', 'metadata'].includes(decision.category) &&
                selectedActionDetail}
              {selected && active?.finding_type === 'reconciliation_release' && (
                <div className={styles.releaseStepLink}>
                  <p>Compare the available editions before choosing this album's track list.</p>
                  <Button
                    type="button"
                    className={styles.secondaryButton}
                    onClick={() => setAlbumView('release')}
                  >
                    Compare releases →
                  </Button>
                </div>
              )}
            </Fragment>
          );
        })}
      </div>
    </section>
  );
}
