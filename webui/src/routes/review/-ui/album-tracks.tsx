import { Button, Select } from '@/components/form/form';

import type { ReviewController } from './use-review-controller';

import { reopenReviewAssignment, type AlbumAssignment } from '../-review.api';
import { CompletenessChoice } from './completeness-choice';
import { MissingTracksChoice } from './missing-tracks-choice';
import { AssignmentEvidence, StatusChip } from './reconciliation-evidence';
import { numberingSchemeLabel } from './review-labels';
import styles from './review-page.module.css';
import { assignmentKey, textValue } from './review-presentation';

type Props = Pick<
  ReviewController,
  | 'act'
  | 'active'
  | 'activeRowKey'
  | 'assignments'
  | 'assignmentsByKey'
  | 'caseId'
  | 'compactSelectedActionDetail'
  | 'decisions'
  | 'filterCount'
  | 'hasIntentDecision'
  | 'inspectorOpen'
  | 'inspectorRef'
  | 'intentFinding'
  | 'matchingFilter'
  | 'metadataDecisionCount'
  | 'missingAssignments'
  | 'missingFindings'
  | 'mutationBusy'
  | 'openGroups'
  | 'pairedConflictCount'
  | 'primaryCandidate'
  | 'proposedChangeCount'
  | 'proposedChangeGroups'
  | 'reconsiderScope'
  | 'refresh'
  | 'releaseChoicePending'
  | 'releaseHasMultipleDiscs'
  | 'releaseOrderedAssignments'
  | 'renderAssignmentRow'
  | 'requestAllMissing'
  | 'reviewCase'
  | 'selectedCaseRef'
  | 'setActiveId'
  | 'setActiveRowKey'
  | 'setAlbumView'
  | 'setBusy'
  | 'setError'
  | 'setInspectorOpen'
  | 'setNotice'
  | 'setTrackFilter'
  | 'setTrackView'
  | 'showTrackList'
  | 'toggleGroup'
  | 'trackFilter'
  | 'trackListScroll'
  | 'trackView'
  | 'visibleAttention'
  | 'visibleChanges'
  | 'visibleFiles'
  | 'visibleMissing'
  | 'visibleSettled'
  | 'visibleUncollected'
  | 'wishlistedCount'
>;

export function AlbumTracks({
  act,
  active,
  activeRowKey,
  assignments,
  assignmentsByKey,
  caseId,
  compactSelectedActionDetail,
  decisions,
  filterCount,
  hasIntentDecision,
  inspectorOpen,
  inspectorRef,
  intentFinding,
  matchingFilter,
  metadataDecisionCount,
  missingAssignments,
  missingFindings,
  mutationBusy,
  openGroups,
  pairedConflictCount,
  primaryCandidate,
  proposedChangeCount,
  proposedChangeGroups,
  reconsiderScope,
  refresh,
  releaseChoicePending,
  releaseHasMultipleDiscs,
  releaseOrderedAssignments,
  renderAssignmentRow,
  requestAllMissing,
  reviewCase,
  selectedCaseRef,
  setActiveId,
  setActiveRowKey,
  setAlbumView,
  setBusy,
  setError,
  setInspectorOpen,
  setNotice,
  setTrackFilter,
  setTrackView,
  showTrackList,
  toggleGroup,
  trackFilter,
  trackListScroll,
  trackView,
  visibleAttention,
  visibleChanges,
  visibleFiles,
  visibleMissing,
  visibleSettled,
  visibleUncollected,
  wishlistedCount,
}: Props) {
  if (!reviewCase) return null;
  return (
    <div className={styles.trackWorkspace} data-detail={inspectorOpen || undefined}>
      <section
        className={styles.trackMatrix}
        aria-label="Complete album track assignment"
        tabIndex={-1}
      >
        <div className={styles.sectionHeading}>
          <div>
            <div className={styles.eyebrow}>Album track list</div>
            <h3>Track assignments</h3>
            <p>Compare each local file with the selected release slot and proposed result.</p>
          </div>
          <div className={styles.matrixCounts}>
            {(reviewCase.album_plan?.counts.confirmed || 0) > 0 && (
              <StatusChip tone="good" icon="check">
                {reviewCase.album_plan?.counts.confirmed} confirmed
              </StatusChip>
            )}
            {proposedChangeCount > 0 && (
              <StatusChip tone="info" icon="arrow">
                {proposedChangeCount} proposed change
                {proposedChangeCount === 1 ? '' : 's'}
              </StatusChip>
            )}
            {metadataDecisionCount > 0 && (
              <StatusChip tone="warning" icon="warning">
                {metadataDecisionCount} metadata decision
                {metadataDecisionCount === 1 ? '' : 's'}
              </StatusChip>
            )}
            {(reviewCase.album_plan?.counts.uncertain || 0) > 0 && (
              <StatusChip tone="warning" icon="warning">
                {reviewCase.album_plan?.counts.uncertain} uncertain
              </StatusChip>
            )}
            {(reviewCase.album_plan?.counts.missing || 0) > 0 && (
              <StatusChip tone="danger" icon="x">
                {reviewCase.album_plan?.counts.missing} missing
              </StatusChip>
            )}
            {wishlistedCount > 0 && (
              <StatusChip tone="info" icon="info">
                {wishlistedCount} wishlisted
              </StatusChip>
            )}
            {(reviewCase.album_plan?.counts.not_owned || 0) > 0 && (
              <StatusChip tone="muted" icon="info">
                {reviewCase.album_plan?.counts.not_owned} outside collection
              </StatusChip>
            )}
            {(reviewCase.album_plan?.counts.settled || 0) > 0 && (
              <StatusChip tone="muted" icon="check">
                {reviewCase.album_plan?.counts.settled} already reviewed
              </StatusChip>
            )}
            {(reviewCase.album_plan?.counts.extra || 0) > 0 && (
              <StatusChip tone="danger" icon="warning">
                {reviewCase.album_plan?.counts.extra} extra
              </StatusChip>
            )}
          </div>
        </div>
        <div className={styles.releaseContext}>
          <div className={styles.releaseReference}>
            <span>{primaryCandidate?.release_title || 'Selected release'}</span>
            <Button variant="ghost" onClick={() => setAlbumView('release')}>
              Compare editions →
            </Button>
          </div>
          {reviewCase.album_plan?.intent_scope && (
            <span>
              {reviewCase.album_plan.intent_inferred
                ? 'One playlist track owned; other tracks are uncollected'
                : `Intent: ${reviewCase.album_plan.intent_scope} album`}
            </span>
          )}
          {reviewCase.album_plan?.candidates?.[0] && (
            <span>
              {numberingSchemeLabel(releaseHasMultipleDiscs, reviewCase.album_plan.numbering_mode)}
            </span>
          )}
        </div>
        {pairedConflictCount > 0 && (
          <p className={styles.versionExplanation}>
            {pairedConflictCount} version conflict
            {pairedConflictCount === 1 ? ' pairs' : 's pair'} a local file with an unmatched release
            slot. Each pair contributes one extra file and one missing slot to the counts above.
          </p>
        )}
        <div className={styles.trackFilters}>
          <div className={styles.trackViewSwitch} role="group" aria-label="Track arrangement">
            <Button
              type="button"
              aria-pressed={trackView === 'issues'}
              onClick={() => setTrackView('issues')}
            >
              Group by action
            </Button>
            <Button
              type="button"
              aria-pressed={trackView === 'release'}
              onClick={() => setTrackView('release')}
            >
              Release order
            </Button>
          </div>
          <label>
            Show tracks
            <Select value={trackFilter} onChange={(event) => setTrackFilter(event.target.value)}>
              <option value="">All tracks ({filterCount('')})</option>
              {trackFilter.startsWith('decision:') && (
                <option value={trackFilter}>
                  {decisions.find((decision) => `decision:${decision.id}` === trackFilter)?.label ||
                    'Selected decision'}{' '}
                  ({filterCount(trackFilter)})
                </option>
              )}
              <option value="uncertain">Uncertain assignments ({filterCount('uncertain')})</option>
              <option value="duplicate">Duplicate files ({filterCount('duplicate')})</option>
              <option value="extra">Unassigned files ({filterCount('extra')})</option>
              <option value="change">All proposed changes ({filterCount('change')})</option>
              <option value="settled">Settled tracks ({filterCount('settled')})</option>
              {proposedChangeGroups.map((group) => (
                <option value={group.key} key={group.key}>
                  {group.label} ({filterCount(group.key)})
                </option>
              ))}
              {missingAssignments.length > 0 && (
                <option value="missing">Missing ({filterCount('missing')})</option>
              )}
              {pairedConflictCount > 0 && (
                <option value="version_conflict">
                  Version conflicts ({filterCount('version_conflict')})
                </option>
              )}
            </Select>
          </label>
          {trackFilter && (
            <Button
              type="button"
              className={styles.secondaryButton}
              onClick={() => setTrackFilter('')}
            >
              Clear filter
            </Button>
          )}
        </div>
        {missingAssignments.length > 0 &&
          !releaseChoicePending &&
          (intentFinding ? (
            <section
              className={styles.missingChoices}
              aria-label="Choose album completeness"
              tabIndex={-1}
            >
              <h4>Choose which missing tracks you want</h4>
              <CompletenessChoice
                key={reviewCase.case_id}
                finding={intentFinding}
                busy={mutationBusy}
                onChoose={(choice) => void act(intentFinding, 'fix', choice)}
              />
            </section>
          ) : missingFindings.length > 0 ? (
            <details
              className={styles.missingRequests}
              open={
                trackFilter === 'missing' ||
                assignmentsByKey.get(activeRowKey || '')?.status === 'missing'
              }
            >
              <summary>
                {missingAssignments.length} missing track
                {missingAssignments.length === 1 ? '' : 's'} · Choose tracks to request
              </summary>
              <MissingTracksChoice
                key={reviewCase.case_id}
                scope={reviewCase.album_plan?.intent_scope}
                findings={missingFindings}
                busy={mutationBusy}
                onRequest={(ids) => void requestAllMissing(ids.length, ids)}
                onChangeScope={() => void reconsiderScope()}
              />
            </details>
          ) : null)}
        <div className={styles.trackTable} role="group" aria-label="Track assignments">
          <div className={styles.trackTableHead} aria-hidden="true">
            <span>Position</span>
            <span>Local file</span>
            <span>Selected release track</span>
            <span>Status</span>
          </div>
          {filterCount(trackFilter) === 0 && (
            <div className={styles.queueEmpty} role="status">
              <p>No tracks match this filter.</p>
              <Button onClick={() => showTrackList('')}>Show all tracks</Button>
            </div>
          )}
          {trackView === 'release'
            ? releaseOrderedAssignments.filter(matchingFilter).map(renderAssignmentRow)
            : [
                {
                  key: 'attention',
                  tone: 'warning',
                  rows: visibleAttention,
                  label: `${visibleAttention.length} track${visibleAttention.length === 1 ? '' : 's'} needing review`,
                },
                {
                  key: 'files',
                  tone: 'danger',
                  rows: visibleFiles,
                  label: `${visibleFiles.length} saved file action${visibleFiles.length === 1 ? '' : 's'}`,
                },
                {
                  key: 'changes',
                  tone: 'info',
                  rows: visibleChanges,
                  label: `↻ ${visibleChanges.length} proposed track change${visibleChanges.length === 1 ? '' : 's'}`,
                },
                {
                  key: 'missing',
                  tone: 'danger',
                  rows: visibleMissing,
                  label: `${visibleMissing.length} absent release track${visibleMissing.length === 1 ? '' : 's'}`,
                },
                {
                  key: 'uncollected',
                  tone: 'neutral',
                  rows: visibleUncollected,
                  label: `${visibleUncollected.length} tracks outside your collection`,
                },
                {
                  key: 'settled',
                  tone: 'good',
                  rows: visibleSettled,
                  label: `✓ ${visibleSettled.length} settled release slot${visibleSettled.length === 1 ? '' : 's'}`,
                },
              ]
                .filter((group) => group.rows.length > 0)
                .map((group) => (
                  <div key={group.key} className={styles.assignmentGroup} data-tone={group.tone}>
                    <Button
                      type="button"
                      aria-expanded={openGroups.includes(group.key)}
                      onClick={() => toggleGroup(group.key)}
                    >
                      {openGroups.includes(group.key) ? '▾' : '▸'} {group.label}
                    </Button>
                    {openGroups.includes(group.key) && (
                      <>
                        {group.key === 'missing' && hasIntentDecision && (
                          <p>Choose missing tracks above, or review album completeness on Plan.</p>
                        )}
                        {group.rows.map(renderAssignmentRow)}
                      </>
                    )}
                  </div>
                ))}
        </div>
      </section>
      <aside
        className={styles.trackInspector}
        ref={inspectorRef}
        tabIndex={-1}
        aria-label="Selected track details"
        data-open={inspectorOpen || undefined}
      >
        <Button
          type="button"
          variant="ghost"
          className={styles.inspectorClose}
          onClick={() => {
            setInspectorOpen(false);
            requestAnimationFrame(() => {
              document
                .querySelector<HTMLElement>(`[data-review-assignment][data-selected="true"]`)
                ?.focus({ preventScroll: true });
              trackListScroll.current.forEach(({ element, top }) => {
                element.scrollTop = top;
              });
            });
          }}
        >
          ← Back to tracks
        </Button>
        {activeRowKey &&
          (() => {
            const rows = assignments.filter(
              (row) =>
                row.finding_id != null || row.historical_finding_id != null || row.planned_choice,
            );
            const index = rows.findIndex((row) => assignmentKey(row) === activeRowKey);
            const go = (row: AlbumAssignment | undefined) => {
              if (!row) return;
              setTrackFilter('');
              setActiveId(row.finding_id ?? null);
              setActiveRowKey(assignmentKey(row));
              setInspectorOpen(true);
            };
            return (
              <nav className={styles.decisionNavigation} aria-label="Track decision navigation">
                <Button disabled={index <= 0} onClick={() => go(rows[index - 1])}>
                  ← Previous decision
                </Button>
                <Button
                  disabled={index < 0 || index >= rows.length - 1}
                  onClick={() => go(rows[index + 1])}
                >
                  Next decision →
                </Button>
              </nav>
            );
          })()}
        {activeRowKey && assignmentsByKey.get(activeRowKey)?.status === 'missing' ? (
          <>
            <AssignmentEvidence
              row={assignmentsByKey.get(activeRowKey)!}
              album={reviewCase}
              controls={
                <div className={styles.inlineActions}>
                  {!releaseChoicePending &&
                    active?.finding_type === 'reconciliation_missing' &&
                    !['wishlisted', 'downloading', 'downloaded'].includes(
                      textValue(active.details?.fulfillment_state),
                    ) && (
                      <Button
                        variant="primary"
                        disabled={mutationBusy}
                        onClick={() => void requestAllMissing(1, [active.id])}
                      >
                        Request this track
                      </Button>
                    )}
                  {!releaseChoicePending && (intentFinding || missingFindings.length > 0) ? (
                    <Button
                      onClick={() => {
                        setInspectorOpen(false);
                        requestAnimationFrame(() => {
                          const choices = document.querySelector<HTMLElement>(
                            '[aria-label="Choose missing tracks"], [aria-label="Choose album completeness"]',
                          );
                          const disclosure = choices?.closest('details');
                          if (disclosure) disclosure.open = true;
                          choices?.focus({ preventScroll: true });
                          choices?.scrollIntoView({ block: 'center' });
                        });
                      }}
                    >
                      Choose missing tracks →
                    </Button>
                  ) : (
                    <Button
                      onClick={() => {
                        setAlbumView('plan');
                        void reconsiderScope();
                      }}
                    >
                      Review album completeness →
                    </Button>
                  )}
                </div>
              }
            />
          </>
        ) : activeRowKey && active ? (
          compactSelectedActionDetail
        ) : activeRowKey && assignments.some((row) => assignmentKey(row) === activeRowKey) ? (
          <AssignmentEvidence
            row={assignmentsByKey.get(activeRowKey)!}
            album={reviewCase}
            busy={mutationBusy}
            onReviewAgain={(findingId) => {
              const requestedCase = caseId;
              setBusy(true);
              setError('');
              void reopenReviewAssignment(requestedCase, findingId)
                .then(async (result) => {
                  if (!result.success) throw new Error(result.error || 'Could not reopen decision');
                  if (selectedCaseRef.current !== requestedCase) return;
                  setNotice(result.message || 'Decision reopened.');
                  await refresh();
                })
                .catch((error: unknown) =>
                  setError(error instanceof Error ? error.message : 'Could not reopen decision'),
                )
                .finally(() => setBusy(false));
            }}
          />
        ) : (
          <p>Select a track to inspect its evidence and decisions.</p>
        )}
      </aside>
    </div>
  );
}
