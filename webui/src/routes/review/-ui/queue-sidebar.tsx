import { Button, Select, TextInput } from '@/components/form/form';
import { Notice } from '@/components/primitives';

import type { ReviewController } from './use-review-controller';

import { ReviewArtwork } from './review-artwork';
import { categoryOptions, queueModes, caseStatusSummary } from './review-labels';
import styles from './review-page.module.css';

type Props = Pick<
  ReviewController,
  | 'caseId'
  | 'caseListRef'
  | 'category'
  | 'page'
  | 'pageCount'
  | 'query'
  | 'queue'
  | 'queueError'
  | 'queueExpanded'
  | 'queueLoading'
  | 'queueMode'
  | 'queueToggleRef'
  | 'queueTotals'
  | 'refreshQueue'
  | 'searchInput'
  | 'selectCase'
  | 'setCaseId'
  | 'setCategory'
  | 'setPage'
  | 'setQuery'
  | 'setQueueExpanded'
  | 'setQueueMode'
  | 'setSearchInput'
>;

export function QueueSidebar({
  caseId,
  caseListRef,
  category,
  page,
  pageCount,
  query,
  queue,
  queueError,
  queueExpanded,
  queueLoading,
  queueMode,
  queueToggleRef,
  queueTotals,
  refreshQueue,
  searchInput,
  selectCase,
  setCaseId,
  setCategory,
  setPage,
  setQuery,
  setQueueExpanded,
  setQueueMode,
  setSearchInput,
}: Props) {
  return (
    <aside className={styles.sidebar} aria-label="Reconciliation albums">
      <Button
        ref={queueToggleRef}
        className={styles.queueToggle}
        aria-expanded={queueExpanded}
        aria-controls="review-album-browser"
        onClick={() => setQueueExpanded((expanded) => !expanded)}
      >
        <span>{queueExpanded ? 'Hide album browser' : 'Browse albums'}</span>
        <span>
          {queueModes.find((mode) => mode.value === queueMode)?.label} · {queue?.total ?? '…'}
          {query || category ? ' · Filtered' : ''}
        </span>
      </Button>
      <div id="review-album-browser" className={styles.queueBrowser} data-expanded={queueExpanded}>
        <nav className={styles.queueModes} aria-label="Queue view">
          {queueModes.map((mode) => (
            <Button
              key={mode.value}
              type="button"
              aria-current={queueMode === mode.value ? 'page' : undefined}
              onClick={() => {
                if (queueMode === mode.value) return;
                setQueueMode(mode.value);
                setPage(0);
                setCaseId('');
              }}
            >
              <span>{mode.label}</span>
              <b>{queueTotals?.[mode.value] ?? '—'}</b>
            </Button>
          ))}
        </nav>
        <label htmlFor="review-search">Find album or track</label>
        <TextInput
          id="review-search"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Search findings"
        />
        <label className={styles.categoryFilter}>
          Filter by type
          <Select
            value={category}
            onChange={(event) => {
              setCategory(event.target.value);
              setPage(0);
              setCaseId('');
            }}
          >
            {categoryOptions.map(([value, label]) => (
              <option key={value || 'all'} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </label>
        {(searchInput || query || category) && (
          <Button
            type="button"
            className={styles.clearFilters}
            onClick={() => {
              setSearchInput('');
              setQuery('');
              setCategory('');
              setPage(0);
            }}
          >
            Clear filters
          </Button>
        )}
        <div className={styles.caseList} ref={caseListRef} aria-busy={queueLoading}>
          {queueLoading && <p role="status">{query ? 'Searching albums…' : 'Loading albums…'}</p>}
          {queueError && (
            <Notice tone="warning" role="alert">
              <p>Could not load albums. {queueError}</p>
              <Button onClick={() => void refreshQueue()}>Retry search</Button>
            </Notice>
          )}
          {queue?.items.map((item) => (
            <Button
              key={item.case_id}
              type="button"
              className={item.case_id === caseId ? styles.selectedCase : styles.caseButton}
              aria-current={item.case_id === caseId ? 'true' : undefined}
              onClick={() => selectCase(item.case_id)}
            >
              <ReviewArtwork
                title={item.current_album_title || item.album || 'Unassigned files'}
                url={item.album_thumb_url}
                size="list"
              />
              <span className={styles.caseCopy}>
                <strong>{item.current_album_title || item.album || 'Unassigned files'}</strong>
                <span>{item.artist || item.case_id.split(':')[0]}</span>
                <small>
                  {caseStatusSummary(item)}
                  {item.error_count
                    ? ` · ${item.error_count} apply issue${item.error_count === 1 ? '' : 's'}`
                    : ''}
                </small>
              </span>
            </Button>
          ))}
          {queue && queue.total === 0 && (
            <div className={styles.queueEmpty}>
              <strong>No albums in this view</strong>
              <p>
                {category
                  ? `There are no ${categoryOptions.find(([value]) => value === category)?.[1].toLowerCase() || 'matching'} albums in ${queueModes.find((mode) => mode.value === queueMode)?.label.toLowerCase()}.`
                  : 'No albums match the current search and queue view.'}
              </p>
              {queueMode !== 'all' && (
                <Button
                  type="button"
                  onClick={() => {
                    setQueueMode('all');
                    setPage(0);
                  }}
                >
                  Show all {category ? 'albums of this type' : 'albums'}
                </Button>
              )}
              {query && (
                <Button
                  type="button"
                  onClick={() => {
                    setSearchInput('');
                    setQuery('');
                  }}
                >
                  Clear search
                </Button>
              )}
            </div>
          )}
        </div>
        <div className={styles.pagination}>
          <Button
            type="button"
            disabled={page === 0}
            onClick={() => {
              setCaseId('');
              setPage(page - 1);
            }}
          >
            Previous
          </Button>
          <span className={styles.paginationCenter}>
            <span>
              Page {page + 1} of {pageCount}
            </span>
            <Select
              aria-label="Jump to page"
              value={page}
              onChange={(event) => {
                setCaseId('');
                setPage(Number(event.target.value));
              }}
            >
              {Array.from({ length: pageCount }, (_, index) => (
                <option key={index} value={index}>
                  Page {index + 1}
                </option>
              ))}
            </Select>
          </span>
          <Button
            type="button"
            disabled={!queue || (page + 1) * queue.limit >= queue.total}
            onClick={() => {
              setCaseId('');
              setPage(page + 1);
            }}
          >
            Next
          </Button>
        </div>
      </div>
    </aside>
  );
}
