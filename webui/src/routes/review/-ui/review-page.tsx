import { Button } from '@/components/form/form';
import { PageHeader } from '@/components/page-header';
import { Notice } from '@/components/primitives';

import { type ReviewDecision } from '../-review.api';
import { AlbumContext } from './album-context';
import { AlbumDecisions } from './album-decisions';
import { AlbumHeader } from './album-header';
import { AlbumRelease } from './album-release';
import { AlbumTracks } from './album-tracks';
import { TrackRequests, FileDecisions } from './file-decisions';
import { QueueSidebar } from './queue-sidebar';
import { StatusChip } from './reconciliation-evidence';
import { AlbumView } from './review-labels';
import styles from './review-page.module.css';
import { libraryRelativePath } from './review-presentation';
import { records, textValue } from './review-presentation';
import { UnidentifiedAlbum } from './unidentified-album';
import { useReviewController } from './use-review-controller';

export function ReviewPage() {
  const {
    act,
    actGroup,
    active,
    activeAssignment,
    activeDecision,
    activeRowKey,
    albumNumber,
    albumReady,
    albumTitleChange,
    albumView,
    applyBusy,
    applyProgress,
    applyReady,
    applyingThisAlbum,
    assignments,
    assignmentsByKey,
    blockedActionCount,
    busy,
    busyDecision,
    caseId,
    caseListRef,
    category,
    choiceCount,
    compactSelectedActionDetail,
    decisions,
    decisionsByFinding,
    dialog,
    error,
    errorCase,
    filterCount,
    folderFinding,
    globalNotice,
    hasIntentDecision,
    inspectReleaseId,
    inspectorOpen,
    inspectorRef,
    intentFinding,
    lastRun,
    leaveAlbum,
    mappedSlots,
    matchingFilter,
    metadataDecisionCount,
    missingAssignments,
    missingFindings,
    mutationBusy,
    nextCaseId,
    notice,
    openDecision,
    openGroups,
    operationCase,
    page,
    pageCount,
    pairedConflictCount,
    pendingDecisions,
    pinCandidate,
    plan,
    previewCandidate,
    previousCaseId,
    primaryCandidate,
    proposedChangeCount,
    proposedChangeGroups,
    query,
    queue,
    queueError,
    queueExpanded,
    queueLoading,
    queueMode,
    queueToggleRef,
    queueTotals,
    readyActionControls,
    readyChangeGroups,
    readyIncludedInGroup,
    readySummaryList,
    reanalyzeAlbum,
    reanalyzing,
    reconsiderScope,
    refresh,
    refreshQueue,
    releaseCandidates,
    releaseChoicePending,
    releaseHasMultipleDiscs,
    releaseIdInput,
    releaseLookupVersion,
    releaseOrderedAssignments,
    renderAssignmentRow,
    requestAllMissing,
    retryOrganization,
    reviewCase,
    reviewDecisions,
    runScan,
    scanBusy,
    scanStatus,
    searchInput,
    selectCase,
    selectedActionDetail,
    selectedCaseRef,
    setActiveId,
    setActiveRowKey,
    setAlbumView,
    setBusy,
    setCaseId,
    setCategory,
    setError,
    setGlobalNotice,
    setInspectorOpen,
    setManualRelease,
    setNotice,
    setPage,
    setPreviewCandidate,
    setQuery,
    setQueueExpanded,
    setQueueMode,
    setReleaseIdInput,
    setSearchInput,
    setTrackFilter,
    setTrackView,
    settledSlots,
    showTrackList,
    sourceFolders,
    stopCollecting,
    toggleGroup,
    totalSlots,
    trackFilter,
    trackListScroll,
    trackView,
    unidentified,
    unidentifiedFinding,
    visibleAttention,
    visibleChanges,
    visibleFiles,
    visibleMissing,
    visibleSettled,
    visibleUncollected,
    wishlistedCount,
  } = useReviewController();
  return (
    <main className={`page-shell review-queue-root ${styles.page}`}>
      {dialog}
      <a href="/tools" className={styles.back}>
        ← Library Maintenance
      </a>
      <PageHeader
        className={styles.queueHeader}
        title="Review Queue"
        subtitle={
          <>
            Review album plans, apply supported changes, and resolve exceptions.
            {lastRun?.status === 'completed' &&
              scanStatus?.current_job?.job_id !== 'library_reconciliation' && (
                <span className={styles.scanHistory}>
                  Last reconciliation: {lastRun.items_scanned} files checked.
                </span>
              )}
          </>
        }
        actions={
          <div className={styles.headerTools}>
            <div className={styles.headerCount}>
              <strong>
                {queueTotals || queue
                  ? `${queueTotals?.all ?? queue?.total} album${(queueTotals?.all ?? queue?.total) === 1 ? '' : 's'}${query || category ? ' in this view' : ''}`
                  : 'Loading queue…'}
              </strong>
              <span>
                {queueTotals?.ready_albums_total != null
                  ? `${queueTotals.ready_albums_total} fully decided album${queueTotals.ready_albums_total === 1 ? '' : 's'} · `
                  : ''}
                {queue
                  ? `${queueTotals?.ready_total ?? queue.ready_total ?? 0} ready change${(queueTotals?.ready_total ?? queue.ready_total ?? 0) === 1 ? '' : 's'} across the library`
                  : 'Loading ready changes…'}
              </span>
            </div>
            <Button
              type="button"
              variant="primary"
              className={styles.safeAction}
              disabled={
                mutationBusy || applyBusy || !(queueTotals?.ready_total ?? queue?.ready_total)
              }
              onClick={() => void applyReady()}
            >
              {applyBusy && !operationCase
                ? applyProgress || 'Applying…'
                : queue
                  ? `Apply ready changes (${queueTotals?.ready_total ?? queue.ready_total ?? 0})`
                  : 'Loading ready changes…'}
            </Button>
            <div className={styles.inlineActions} aria-label="Queue actions">
              <Button
                type="button"
                title="Recheck files and matching evidence, then rebuild album plans"
                disabled={scanBusy || scanStatus?.current_job?.job_id === 'library_reconciliation'}
                onClick={() => {
                  void runScan();
                }}
              >
                {scanBusy
                  ? 'Queuing…'
                  : scanStatus?.current_job?.job_id === 'library_reconciliation'
                    ? 'Scanning…'
                    : 'Reanalyze library'}
              </Button>
            </div>
          </div>
        }
      />

      {error && (!errorCase || !reviewCase) && (
        <Notice tone="danger" role="alert" aria-label="Queue error">
          {error}
        </Notice>
      )}
      {globalNotice && (
        <Notice tone="info" role="status" className={styles.queueNotice}>
          <span>{globalNotice}</span>
          <Button
            type="button"
            aria-label="Dismiss queue notice"
            onClick={() => setGlobalNotice('')}
          >
            ×
          </Button>
        </Notice>
      )}
      {notice && !reviewCase && (
        <div className={styles.notice} role="status">
          {notice}
        </div>
      )}
      {applyBusy && !operationCase && (
        <Notice tone="info" className={styles.applyBanner} role="status" aria-live="polite">
          <span className={styles.loadingSpinner} aria-hidden="true" />
          <strong>{applyProgress || 'Applying album changes…'}</strong>
          <span>This continues on the server. You can leave this page and return.</span>
        </Notice>
      )}
      {scanStatus?.current_job?.job_id === 'library_reconciliation' && (
        <div className={styles.scanStatus} role="status" data-running>
          <span className={styles.loadingSpinner} aria-hidden="true" />
          <progress
            aria-label="Library reconciliation progress"
            max={100}
            value={
              scanStatus.current_job.progress?.total
                ? scanStatus.current_job.progress.percent || 0
                : undefined
            }
          />
          {scanStatus.current_job.progress?.total
            ? `Reconciliation running: ${scanStatus.current_job.progress.scanned} of ${scanStatus.current_job.progress.total} folders.`
            : 'Discovering album folders…'}
        </div>
      )}
      {lastRun &&
        lastRun.status !== 'completed' &&
        scanStatus?.current_job?.job_id !== 'library_reconciliation' && (
          <details className={styles.scanStatus}>
            <summary>Previous library scan: {lastRun.status}</summary>
            <p>
              {lastRun.error_text || 'No reason recorded'}. {lastRun.items_scanned} files checked; a
              rerun reuses cached fingerprints.
            </p>
          </details>
        )}

      <div className={styles.workspace}>
        <QueueSidebar
          caseId={caseId}
          caseListRef={caseListRef}
          category={category}
          page={page}
          pageCount={pageCount}
          query={query}
          queue={queue}
          queueError={queueError}
          queueExpanded={queueExpanded}
          queueLoading={queueLoading}
          queueMode={queueMode}
          queueToggleRef={queueToggleRef}
          queueTotals={queueTotals}
          refreshQueue={refreshQueue}
          searchInput={searchInput}
          selectCase={selectCase}
          setCaseId={setCaseId}
          setCategory={setCategory}
          setPage={setPage}
          setQuery={setQuery}
          setQueueExpanded={setQueueExpanded}
          setQueueMode={setQueueMode}
          setSearchInput={setSearchInput}
        />

        <section
          className={styles.review}
          aria-label="Current review case"
          data-refresh={reviewCase?.refresh?.state === 'running' ? 'running' : undefined}
        >
          {reviewCase ? (
            <>
              <AlbumHeader
                albumNumber={albumNumber}
                albumTitleChange={albumTitleChange}
                leaveAlbum={leaveAlbum}
                mutationBusy={mutationBusy}
                nextCaseId={nextCaseId}
                previousCaseId={previousCaseId}
                queue={queue}
                reanalyzeAlbum={reanalyzeAlbum}
                reviewCase={reviewCase}
                selectCase={selectCase}
                wishlistedCount={wishlistedCount}
              />

              {reviewCase.last_operation?.error && (
                <details className={styles.operationHistory}>
                  <summary>
                    Previous attempt
                    {reviewCase.last_operation.updated_at
                      ? ` · ${new Date(reviewCase.last_operation.updated_at * 1000).toLocaleString()}`
                      : ''}
                    {' · '}
                    {reviewCase.last_operation.fixed || 0} actions applied
                    {reviewCase.last_operation.organize_failed
                      ? ' · folder step incomplete'
                      : ' · needs attention'}
                  </summary>
                  <p>{reviewCase.last_operation.error}</p>
                  <p>Completed metadata changes are retained.</p>
                  {reviewCase.organization_retry_signature && (
                    <Button disabled={mutationBusy} onClick={() => void retryOrganization()}>
                      Review current folder changes
                    </Button>
                  )}
                </details>
              )}
              {error && errorCase === reviewCase.case_id && !reviewCase.last_operation?.error && (
                <Notice tone="danger" role="alert" className={styles.queueNotice}>
                  <span>{error}</span>
                  {error.includes('after the scan') && (
                    <Button disabled={mutationBusy} onClick={() => void reanalyzeAlbum()}>
                      Recheck this album
                    </Button>
                  )}
                </Notice>
              )}
              {!folderFinding && (
                <nav
                  className={styles.albumViews}
                  role="tablist"
                  aria-label="Album review views"
                  onKeyDown={(event) => {
                    const views: AlbumView[] = ['plan', 'release', 'tracks'];
                    const index = views.indexOf(albumView);
                    const next =
                      event.key === 'ArrowRight'
                        ? views[(index + 1) % 3]
                        : event.key === 'ArrowLeft'
                          ? views[(index + 2) % 3]
                          : event.key === 'Home'
                            ? views[0]
                            : event.key === 'End'
                              ? views[2]
                              : null;
                    if (next) {
                      event.preventDefault();
                      if (next === 'tracks') showTrackList(trackFilter, false);
                      else setAlbumView(next);
                      document.getElementById(`review-tab-${next}`)?.focus();
                    }
                  }}
                >
                  <Button
                    type="button"
                    role="tab"
                    id="review-tab-plan"
                    aria-controls="review-tab-panel"
                    aria-selected={albumView === 'plan'}
                    tabIndex={albumView === 'plan' ? 0 : -1}
                    aria-current={albumView === 'plan' ? 'page' : undefined}
                    onClick={() => setAlbumView('plan')}
                  >
                    Plan
                    <span>
                      {reviewDecisions.length
                        ? `${reviewDecisions.length} decision${reviewDecisions.length === 1 ? '' : 's'}`
                        : `${plan.ready} action${plan.ready === 1 ? '' : 's'}`}
                    </span>
                  </Button>
                  <Button
                    type="button"
                    role="tab"
                    id="review-tab-release"
                    aria-controls="review-tab-panel"
                    aria-selected={albumView === 'release'}
                    tabIndex={albumView === 'release' ? 0 : -1}
                    aria-current={albumView === 'release' ? 'page' : undefined}
                    onClick={() => setAlbumView('release')}
                  >
                    Release
                  </Button>
                  <Button
                    type="button"
                    role="tab"
                    id="review-tab-tracks"
                    aria-controls="review-tab-panel"
                    aria-selected={albumView === 'tracks'}
                    tabIndex={albumView === 'tracks' ? 0 : -1}
                    aria-current={albumView === 'tracks' ? 'page' : undefined}
                    onClick={() => showTrackList(trackFilter, false)}
                  >
                    Tracks
                    <span>{assignments.length}</span>
                  </Button>
                </nav>
              )}

              {applyBusy && operationCase === reviewCase.case_id && (
                <Notice tone="info" className={styles.applyBanner} role="status" aria-live="polite">
                  <span className={styles.loadingSpinner} aria-hidden="true" />
                  <strong>{applyProgress || 'Applying this album plan…'}</strong>
                  <span>This continues on the server. You can leave this page and return.</span>
                </Notice>
              )}
              {notice && !reanalyzing && reviewCase.refresh?.state !== 'running' && (
                <Notice
                  tone="info"
                  className={styles.dismissibleNotice}
                  role="status"
                  aria-live="polite"
                >
                  <span>{notice}</span>
                  <Button type="button" aria-label="Dismiss notice" onClick={() => setNotice('')}>
                    ×
                  </Button>
                </Notice>
              )}
              {!(applyBusy && operationCase === reviewCase.case_id) &&
                (reanalyzing || reviewCase.refresh?.state === 'running') && (
                  <Notice tone="info" className={styles.refreshBanner} role="status">
                    <span className={styles.loadingSpinner} aria-hidden="true" />
                    {reviewCase.refresh?.scope === 'plan'
                      ? 'Updating the album plan from the evidence already reviewed. Your saved choices remain visible.'
                      : 'Reanalyzing this album. The previous decisions remain visible while dependent track actions are recalculated.'}
                  </Notice>
                )}

              {albumView === 'plan' &&
                !releaseChoicePending &&
                reviewCase.album_plan?.assignments.some((row) =>
                  ['missing', 'not_owned'].includes(row.status),
                ) && (
                  <section className={styles.completenessSummary} aria-label="Album completeness">
                    <div>
                      <h4>Album completeness</h4>
                      <p>
                        {intentFinding
                          ? 'Not decided'
                          : reviewCase.album_plan.intent_scope === 'discard'
                            ? 'Collection stopped · file removals planned'
                            : reviewCase.album_plan.intent_scope === 'complete'
                              ? 'Collect the complete album'
                              : reviewCase.album_plan.intent_scope === 'partial'
                                ? 'Keep a partial album'
                                : 'Not decided'}
                      </p>
                    </div>
                    {intentFinding ? (
                      <Button
                        disabled={mutationBusy}
                        onClick={() => {
                          setAlbumView('tracks');
                          setTrackFilter('missing');
                          setInspectorOpen(false);
                        }}
                      >
                        Choose missing tracks
                      </Button>
                    ) : (
                      <Button disabled={mutationBusy} onClick={() => void reconsiderScope()}>
                        Change completeness…
                      </Button>
                    )}
                  </section>
                )}

              {albumView === 'tracks' && albumReady && (
                <Notice tone="info" className={styles.albumNotice} role="status">
                  <span>All decisions are planned. This album is ready to apply.</span>
                  <Button type="button" onClick={() => setAlbumView('plan')}>
                    Return to plan →
                  </Button>
                </Notice>
              )}
              <div
                className={styles.tabWorkspace}
                data-view={albumView}
                data-unidentified={unidentified || undefined}
              >
                <div
                  className={styles.tabMain}
                  id="review-tab-panel"
                  role="tabpanel"
                  aria-labelledby={`review-tab-${albumView}`}
                >
                  {unidentified && albumView !== 'release' && (
                    <UnidentifiedAlbum
                      reviewCase={reviewCase}
                      busy={mutationBusy}
                      onIdentify={() => setAlbumView('release')}
                      onLeave={
                        unidentifiedFinding
                          ? () => void act(unidentifiedFinding, 'dismiss')
                          : undefined
                      }
                    />
                  )}
                  {albumView === 'plan' && reviewCase.can_stop_collecting && (
                    <section className={styles.abandonAlbum}>
                      <div>
                        <h3>All remaining files have removal planned</h3>
                        <p>
                          If you do not want this album, stop collecting it before applying the file
                          removals.
                        </p>
                      </div>
                      <Button disabled={mutationBusy} onClick={() => void stopCollecting()}>
                        I don’t want this album
                      </Button>
                    </section>
                  )}
                  {folderFinding && (
                    <section
                      className={styles.folderConflict}
                      aria-label="Folder membership review"
                    >
                      <StatusChip tone="warning" icon="warning">
                        Album ownership needs review
                      </StatusChip>
                      <h3>{folderFinding.title}</h3>
                      <p>{folderFinding.description}</p>
                      <p>
                        {textValue(folderFinding.details?.file_count)} local files were found. A
                        release plan cannot be built until their album ownership is consistent.
                      </p>
                      <p>
                        Check the album tags and folder placement below. Correct the conflicting
                        membership, then reanalyze this album.
                      </p>
                      <p className={styles.muted}>
                        {libraryRelativePath(
                          reviewCase.folder || '',
                          reviewCase.album_plan?.library_root,
                        )}
                      </p>
                      <ul className={styles.folderFiles}>
                        {records(folderFinding.details?.checked_files).map((file) => (
                          <li key={textValue(file.file_path)}>
                            <strong>{textValue(file.title)}</strong>
                            <span>
                              {[file.album, file.artist].map(textValue).filter(Boolean).join(' · ')}
                            </span>
                            <small>{textValue(file.file_path)}</small>
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                  {albumView === 'plan' && albumReady && (
                    <section className={styles.albumApplyAll} aria-label="Apply album changes">
                      <div>
                        <strong>
                          Plan ready to apply · {plan.ready} action{plan.ready === 1 ? '' : 's'}
                        </strong>
                        <p>
                          All required decisions are planned and saved. Apply now or leave this
                          album for the next ready batch.
                        </p>
                      </div>
                      {readyActionControls}
                      <div className={styles.readyChanges}>
                        <h4>Planned changes</h4>
                        {readyChangeGroups.length > 0 && (
                          <p>Choose a field to inspect the affected tracks and exact values.</p>
                        )}
                        {readySummaryList}
                        <TrackRequests items={reviewCase.items} />
                        <FileDecisions
                          items={reviewCase.items}
                          onReview={(id) => {
                            const decision = decisionsByFinding.get(id);
                            if (decision) openDecision({ ...decision, finding_ids: [id] });
                          }}
                        />
                        {active &&
                          activeDecision?.risk === 'ready' &&
                          !activeAssignment &&
                          selectedActionDetail}
                      </div>
                    </section>
                  )}

                  {albumView === 'plan' &&
                    !albumReady &&
                    !folderFinding &&
                    !unidentified &&
                    (reviewDecisions.length > 0 || pendingDecisions.length === 0) &&
                    (reviewCase.album_plan || decisions.length > 0) && (
                      <AlbumDecisions
                        act={act}
                        actGroup={actGroup}
                        active={active}
                        activeAssignment={activeAssignment}
                        applyingThisAlbum={applyingThisAlbum}
                        blockedActionCount={blockedActionCount}
                        busy={busy}
                        busyDecision={busyDecision}
                        choiceCount={choiceCount}
                        mutationBusy={mutationBusy}
                        openDecision={openDecision}
                        plan={plan}
                        releaseChoicePending={releaseChoicePending}
                        releaseHasMultipleDiscs={releaseHasMultipleDiscs}
                        requestAllMissing={requestAllMissing}
                        reviewCase={reviewCase}
                        reviewDecisions={reviewDecisions}
                        selectedActionDetail={selectedActionDetail}
                        setAlbumView={setAlbumView}
                        showTrackList={showTrackList}
                      />
                    )}

                  {albumView === 'plan' &&
                    plan.status !== 'ready' &&
                    plan.ready > 0 &&
                    !readyIncludedInGroup && (
                      <section
                        className={`${styles.albumPlan} ${styles.safeChangesPanel}`}
                        data-state="supporting"
                        aria-label="Independent safe changes"
                      >
                        <div className={styles.planSummary}>
                          <div>
                            <div className={styles.eyebrow}>Planned changes</div>
                            <strong>
                              {plan.ready} supported change{plan.ready === 1 ? '' : 's'}
                            </strong>
                            <p>
                              {plan.status === 'waiting'
                                ? 'These edits stay in your plan while the requested files are received and assigned.'
                                : 'Resolve the remaining album decisions before applying these edits.'}
                            </p>
                          </div>
                          <div className={styles.planBadges}>
                            <StatusChip tone="good" icon="check">
                              {plan.ready} planned
                            </StatusChip>
                          </div>
                        </div>
                        {plan.status === 'waiting' && readySummaryList}
                        <Button
                          type="button"
                          className={styles.secondaryButton}
                          onClick={() => {
                            showTrackList('change');
                          }}
                        >
                          Inspect proposed track edits →
                        </Button>

                        {active &&
                          activeDecision?.risk === 'ready' &&
                          !activeAssignment &&
                          selectedActionDetail}
                      </section>
                    )}

                  {albumView === 'plan' &&
                    reviewDecisions.length === 0 &&
                    pendingDecisions.length === 0 &&
                    plan.ready === 0 && (
                      <div className={styles.empty}>No album-level action is pending.</div>
                    )}

                  {reviewCase.replacement_operations?.map((operation) => (
                    <Notice key={operation.file} tone={operation.error ? 'warning' : 'info'}>
                      <strong>Replacing {operation.file}</strong>
                      <p>
                        {operation.error ||
                          'The current copy stays playable until the new copy is verified and its playlist references are transferred.'}
                      </p>
                    </Notice>
                  ))}

                  {albumView === 'plan' && pendingDecisions.length > 0 && (
                    <section
                      className={styles.pendingFulfillment}
                      aria-label="Missing tracks being acquired"
                    >
                      <StatusChip tone="info" icon="info">
                        {wishlistedCount} requested
                      </StatusChip>
                      <div>
                        <strong>Waiting for missing tracks</strong>
                        <p>
                          {wishlistedCount === 1
                            ? 'The missing track is'
                            : 'These missing tracks are'}{' '}
                          already requested. Published downloads are checked automatically; the
                          album stays here until those files are assigned.
                        </p>
                        <Button
                          nativeButton={false}
                          role="link"
                          render={
                            <a
                              href={`/wishlist?q=${encodeURIComponent(reviewCase?.album || '')}`}
                            />
                          }
                        >
                          View album in Wishlist →
                        </Button>
                      </div>
                    </section>
                  )}

                  {albumView === 'plan' &&
                    reviewCase.items.some((item) => item.details?.planned_choice) && (
                      <section className={styles.editPlan} aria-label="Edit saved choices">
                        <small>Saved choices</small>
                        <div className={styles.inlineActions}>
                          {[
                            ...new Set(
                              reviewCase.items.flatMap((item) => {
                                const choice = item.details?.planned_choice as
                                  | { category?: string }
                                  | undefined;
                                return [
                                  ...(choice?.category ? [choice.category] : []),
                                  ...(item.details?.numbering_choice ? ['numbering'] : []),
                                ];
                              }),
                            ),
                          ].map((category) => (
                            <Button
                              variant="ghost"
                              key={category}
                              type="button"
                              disabled={mutationBusy}
                              onClick={() => void actGroup({ category } as ReviewDecision, 'reset')}
                            >
                              Review {category.replaceAll('_', ' ')} again
                            </Button>
                          ))}
                        </div>
                      </section>
                    )}

                  {albumView === 'tracks' && !unidentified && assignments.length > 0 && (
                    <AlbumTracks
                      act={act}
                      active={active}
                      activeRowKey={activeRowKey}
                      assignments={assignments}
                      assignmentsByKey={assignmentsByKey}
                      caseId={caseId}
                      compactSelectedActionDetail={compactSelectedActionDetail}
                      decisions={decisions}
                      filterCount={filterCount}
                      hasIntentDecision={hasIntentDecision}
                      inspectorOpen={inspectorOpen}
                      inspectorRef={inspectorRef}
                      intentFinding={intentFinding}
                      matchingFilter={matchingFilter}
                      metadataDecisionCount={metadataDecisionCount}
                      missingAssignments={missingAssignments}
                      missingFindings={missingFindings}
                      mutationBusy={mutationBusy}
                      openGroups={openGroups}
                      pairedConflictCount={pairedConflictCount}
                      primaryCandidate={primaryCandidate}
                      proposedChangeCount={proposedChangeCount}
                      proposedChangeGroups={proposedChangeGroups}
                      reconsiderScope={reconsiderScope}
                      refresh={refresh}
                      releaseChoicePending={releaseChoicePending}
                      releaseHasMultipleDiscs={releaseHasMultipleDiscs}
                      releaseOrderedAssignments={releaseOrderedAssignments}
                      renderAssignmentRow={renderAssignmentRow}
                      requestAllMissing={requestAllMissing}
                      reviewCase={reviewCase}
                      selectedCaseRef={selectedCaseRef}
                      setActiveId={setActiveId}
                      setActiveRowKey={setActiveRowKey}
                      setAlbumView={setAlbumView}
                      setBusy={setBusy}
                      setError={setError}
                      setInspectorOpen={setInspectorOpen}
                      setNotice={setNotice}
                      setTrackFilter={setTrackFilter}
                      setTrackView={setTrackView}
                      showTrackList={showTrackList}
                      toggleGroup={toggleGroup}
                      trackFilter={trackFilter}
                      trackListScroll={trackListScroll}
                      trackView={trackView}
                      visibleAttention={visibleAttention}
                      visibleChanges={visibleChanges}
                      visibleFiles={visibleFiles}
                      visibleMissing={visibleMissing}
                      visibleSettled={visibleSettled}
                      visibleUncollected={visibleUncollected}
                      wishlistedCount={wishlistedCount}
                    />
                  )}

                  {albumView === 'tracks' && !unidentified && assignments.length === 0 && (
                    <>
                      <div className={styles.empty}>
                        No track assignment data is available for this album.
                      </div>
                      {active && selectedActionDetail}
                    </>
                  )}

                  {albumView === 'release' && (
                    <AlbumRelease
                      act={act}
                      active={active}
                      busy={busy}
                      inspectReleaseId={inspectReleaseId}
                      mutationBusy={mutationBusy}
                      pinCandidate={pinCandidate}
                      previewCandidate={previewCandidate}
                      releaseCandidates={releaseCandidates}
                      releaseIdInput={releaseIdInput}
                      releaseLookupVersion={releaseLookupVersion}
                      reviewCase={reviewCase}
                      setManualRelease={setManualRelease}
                      setPreviewCandidate={setPreviewCandidate}
                      setReleaseIdInput={setReleaseIdInput}
                    />
                  )}

                  {albumView === 'plan' &&
                    !unidentified &&
                    active &&
                    !activeAssignment &&
                    !activeDecision &&
                    selectedActionDetail}
                </div>
                {albumView !== 'tracks' && (
                  <AlbumContext
                    actGroup={actGroup}
                    albumReady={albumReady}
                    albumTitleChange={albumTitleChange}
                    albumView={albumView}
                    assignments={assignments}
                    caseId={caseId}
                    hasIntentDecision={hasIntentDecision}
                    mappedSlots={mappedSlots}
                    mutationBusy={mutationBusy}
                    plan={plan}
                    previewCandidate={previewCandidate}
                    primaryCandidate={primaryCandidate}
                    proposedChangeGroups={proposedChangeGroups}
                    reconsiderScope={reconsiderScope}
                    releaseCandidates={releaseCandidates}
                    releaseChoicePending={releaseChoicePending}
                    releaseHasMultipleDiscs={releaseHasMultipleDiscs}
                    reviewCase={reviewCase}
                    reviewDecisions={reviewDecisions}
                    setAlbumView={setAlbumView}
                    setPreviewCandidate={setPreviewCandidate}
                    settledSlots={settledSlots}
                    showTrackList={showTrackList}
                    sourceFolders={sourceFolders}
                    totalSlots={totalSlots}
                    unidentified={unidentified}
                    wishlistedCount={wishlistedCount}
                  />
                )}
              </div>
            </>
          ) : queue && queue.total === 0 ? (
            <div className={styles.empty}>
              No pending library reconciliation findings. Run a scan to check the library.
            </div>
          ) : (
            <div className={styles.empty}>Select an album to review.</div>
          )}
        </section>
      </div>
    </main>
  );
}
