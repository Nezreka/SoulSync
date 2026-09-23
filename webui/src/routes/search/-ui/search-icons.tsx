/** the handful of line icons the search page draws. one stroke weight, one size box. */

const common = {
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

export const CaretIcon = ({ className }: { className?: string }) => (
  <svg {...common} viewBox="0 0 12 12" className={className}>
    <path d="M3 4.5 6 7.5 9 4.5" />
  </svg>
);

export const DownloadIcon = () => (
  <svg {...common} strokeWidth={1.8}>
    <path d="M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10" />
  </svg>
);

export const PlayIcon = () => (
  <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
    <path d="M4.5 2.8v10.4a.6.6 0 0 0 .9.5l8.2-5.2a.6.6 0 0 0 0-1L5.4 2.3a.6.6 0 0 0-.9.5z" />
  </svg>
);

export const ClockIcon = () => (
  <svg {...common}>
    <circle cx="8" cy="8" r="5.5" />
    <path d="M8 5v3l2 1.5" />
  </svg>
);

export const DiscIcon = () => (
  <svg {...common}>
    <circle cx="8" cy="8" r="5.5" />
    <circle cx="8" cy="8" r="1.4" />
  </svg>
);

export const FilmIcon = () => (
  <svg {...common}>
    <rect x="2.5" y="3.5" width="11" height="9" rx="2" />
    <path d="M6.5 6.2v3.6L9.8 8z" fill="currentColor" />
  </svg>
);

export const FileIcon = () => (
  <svg {...common}>
    <path d="M4 2.5h5l3 3v8H4z" />
    <path d="M9 2.5v3h3" />
  </svg>
);
