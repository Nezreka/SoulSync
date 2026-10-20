type Props = { className?: string };

/** Marks an artist whose downloads are filed in the audiobook library. */
export function AudiobookRouteBadge({ className }: Props) {
  return (
    <span
      className={className}
      data-audiobook-route-badge=""
      title="Downloads by this artist are filed in your audiobook library"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '1px 8px',
        borderRadius: 999,
        fontSize: '0.75em',
        background: 'rgba(255, 183, 77, 0.16)',
        color: 'rgb(255, 183, 77)',
        whiteSpace: 'nowrap',
      }}
    >
      📖 Audiobook
    </span>
  );
}
