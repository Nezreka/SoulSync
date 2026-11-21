/** the player's icon set: one stroke weight, 24 grid, currentColor */

import type { ReactNode, SVGProps } from 'react';

function Icon({ children, ...props }: SVGProps<SVGSVGElement> & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export const HomeIcon = () => (
  <Icon>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.5V21h14V9.5" />
  </Icon>
);

export const MusicIcon = () => (
  <Icon>
    <path d="M9 18V5l11-2v13" />
    <circle cx="6" cy="18" r="3" />
    <circle cx="17" cy="16" r="3" />
  </Icon>
);

export const MovieIcon = () => (
  <Icon>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" />
  </Icon>
);

export const TvIcon = () => (
  <Icon>
    <rect x="3" y="6" width="18" height="13" rx="2" />
    <path d="m8 2 4 4 4-4" />
  </Icon>
);

export const YoutubeIcon = () => (
  <Icon>
    <rect x="2.5" y="5" width="19" height="14" rx="4" />
    <path d="m10 9.5 5 2.5-5 2.5z" fill="currentColor" />
  </Icon>
);

export const PodcastIcon = () => (
  <Icon>
    <circle cx="12" cy="11" r="2" />
    <path d="M12 13v8" />
    <path d="M7.8 15.5a6 6 0 1 1 8.4 0" />
    <path d="M5 18.5a10 10 0 1 1 14 0" />
  </Icon>
);

export const BookIcon = () => (
  <Icon>
    <path d="M4 19.5V5a2 2 0 0 1 2-2h14v16H6a2 2 0 0 0-2 2" />
    <path d="M4 19.5A2 2 0 0 0 6 21.5h14" />
  </Icon>
);

export const GuideIcon = () => (
  <Icon>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M3 9h18M9 9v11M15 9v11" />
  </Icon>
);

export const SearchIcon = () => (
  <Icon>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </Icon>
);

export const PlayIcon = () => (
  <Icon>
    <path d="M7 4.5v15l12-7.5z" fill="currentColor" />
  </Icon>
);

export const PauseIcon = () => (
  <Icon>
    <path d="M8 5v14M16 5v14" />
  </Icon>
);

export const PrevIcon = () => (
  <Icon>
    <path d="M18 6v12L9 12z" fill="currentColor" />
    <path d="M6 6v12" />
  </Icon>
);

export const NextIcon = () => (
  <Icon>
    <path d="M6 6v12l9-6z" fill="currentColor" />
    <path d="M18 6v12" />
  </Icon>
);

export const ShuffleIcon = () => (
  <Icon>
    <path d="M16 3h5v5M4 20 21 3M21 16v5h-5M15 15l6 6M4 4l5 5" />
  </Icon>
);

export const MoreIcon = () => (
  <Icon>
    <circle cx="5" cy="12" r="1.2" fill="currentColor" />
    <circle cx="12" cy="12" r="1.2" fill="currentColor" />
    <circle cx="19" cy="12" r="1.2" fill="currentColor" />
  </Icon>
);

export const BackIcon = () => (
  <Icon>
    <path d="m15 18-6-6 6-6" />
  </Icon>
);

export const ChevronIcon = () => (
  <Icon>
    <path d="m9 18 6-6-6-6" />
  </Icon>
);

export const ExitIcon = () => (
  <Icon>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
    <path d="m16 17 5-5-5-5M21 12H9" />
  </Icon>
);

export const LockIcon = () => (
  <Icon>
    <rect x="4" y="11" width="16" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </Icon>
);

export const ChevronDownIcon = () => (
  <Icon>
    <path d="m6 9 6 6 6-6" />
  </Icon>
);

export const CheckIcon = () => (
  <Icon strokeWidth={2.6}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Icon>
);

export const GridIcon = () => (
  <Icon>
    <rect x="4" y="4" width="7" height="7" rx="1.5" />
    <rect x="13" y="4" width="7" height="7" rx="1.5" />
    <rect x="4" y="13" width="7" height="7" rx="1.5" />
    <rect x="13" y="13" width="7" height="7" rx="1.5" />
  </Icon>
);

export const ListIcon = () => (
  <Icon>
    <path d="M9 6h11M9 12h11M9 18h11" />
    <circle cx="4.5" cy="6" r="1" fill="currentColor" />
    <circle cx="4.5" cy="12" r="1" fill="currentColor" />
    <circle cx="4.5" cy="18" r="1" fill="currentColor" />
  </Icon>
);
