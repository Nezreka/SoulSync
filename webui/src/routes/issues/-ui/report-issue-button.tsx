import styles from './report-issue-button.module.css';

/**
 * a quiet "report a problem" button for pages outside the issues route.
 * it goes through window.showReportIssueModal like every other entry point,
 * so the composer stays the one in IssueDomainHost.
 */
export function ReportIssueButton({
  id,
  entityType,
  entityId,
  entityName,
  artistName = '',
  albumTitle,
}: {
  id?: string;
  entityType: 'artist' | 'album' | 'track';
  entityId: unknown;
  entityName: string;
  artistName?: string;
  albumTitle?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      className={styles.reportButton}
      title={`Report a problem with this ${entityType}`}
      aria-label={`Report a problem with ${entityName || `this ${entityType}`}`}
      onClick={() =>
        window.showReportIssueModal?.(entityType, entityId, entityName, artistName, albumTitle)
      }
    >
      <svg
        viewBox="0 0 24 24"
        width="13"
        height="13"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M4 22V4a1 1 0 0 1 1-1h11l-2 4 2 4H5" />
      </svg>
      <span>Report</span>
    </button>
  );
}
