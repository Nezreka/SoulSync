/** the player's icon set: one stroke weight, 24 grid, currentColor */

import type { ReactNode, SVGProps } from 'react';

export function Icon({ children, ...props }: SVGProps<SVGSVGElement> & { children: ReactNode }) {
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

export const InfoIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5.5M12 7.5v.01" />
  </Icon>
);

export const PlusIcon = () => (
  <Icon strokeWidth={2.2}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);

/** get it: onto the wishlist, soulsync downloads it */
export const DownloadIcon = () => (
  <Icon>
    <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" />
    <path d="M5 19h14" />
  </Icon>
);

export const BookmarkIcon = ({ filled }: { filled?: boolean }) => (
  <Icon fill={filled ? 'currentColor' : 'none'}>
    <path d="M7 4h10a1 1 0 0 1 1 1v15l-6-3.6L6 20V5a1 1 0 0 1 1-1Z" />
  </Icon>
);

export const WatchlistIcon = () => (
  <Icon>
    <path d="M7 4h10a1 1 0 0 1 1 1v15l-6-3.6L6 20V5a1 1 0 0 1 1-1Z" />
  </Icon>
);

export const PlaylistIcon = () => (
  <Icon>
    <path d="M3 6h12M3 11h12M3 16h7" />
    <circle cx="17" cy="17" r="3" />
    <path d="M20 17V6l2 1" />
  </Icon>
);

export const HeartIcon = ({ filled }: { filled?: boolean }) => (
  <Icon fill={filled ? 'currentColor' : 'none'}>
    <path d="M12 20s-7-4.4-9-9.2C1.6 7.3 3.9 4 7.3 4c2 0 3.5 1.1 4.7 2.7C13.2 5.1 14.7 4 16.7 4 20.1 4 22.4 7.3 21 10.8 19 15.6 12 20 12 20Z" />
  </Icon>
);

export const TrashIcon = () => (
  <Icon>
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
  </Icon>
);

export const PencilIcon = () => (
  <Icon>
    <path d="M4 20h4L19 9l-4-4L4 16v4ZM13.5 6.5l4 4" />
  </Icon>
);

export const GripIcon = () => (
  <Icon>
    <path d="M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01" strokeWidth={2.6} />
  </Icon>
);

export const CopyIcon = () => (
  <Icon>
    <rect x="8" y="8" width="12" height="12" rx="2" />
    <path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" />
  </Icon>
);

export const XIcon = () => (
  <Icon>
    <path d="M6 6l12 12M18 6 6 18" />
  </Icon>
);

export const PeopleIcon = () => (
  <Icon>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3 19c.6-3.2 3-5 6-5s5.4 1.8 6 5" />
    <circle cx="17" cy="9" r="2.4" />
    <path d="M16.5 14.2c2.4.1 4 1.6 4.5 4.3" />
  </Icon>
);

export const GearIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.8v2.4M12 18.8v2.4M4.2 7.5l2.1 1.2M17.7 15.3l2.1 1.2M4.2 16.5l2.1-1.2M17.7 8.7l2.1-1.2" />
    <circle cx="12" cy="12" r="7" />
  </Icon>
);

export const PhoneIcon = () => (
  <Icon>
    <rect x="6.5" y="2.5" width="11" height="19" rx="2.5" />
    <path d="M10.5 18.5h3" />
  </Icon>
);

export const LaptopIcon = () => (
  <Icon>
    <rect x="4" y="4.5" width="16" height="11" rx="1.8" />
    <path d="M2 19.5h20" />
  </Icon>
);

export const SparkIcon = () => (
  <Icon>
    <path d="M12 3.5 13.8 9 19.5 10.5 13.8 12 12 17.5 10.2 12 4.5 10.5 10.2 9Z" />
    <path d="M18.5 16.5 19.2 18.3 21 19 19.2 19.7 18.5 21.5 17.8 19.7 16 19 17.8 18.3Z" />
  </Icon>
);

/** home videos: a camcorder */
export const CamcorderIcon = () => (
  <Icon>
    <rect x="2.5" y="6.5" width="13" height="11" rx="2.5" />
    <path d="m15.5 10.5 6-3.5v10l-6-3.5" />
    <circle cx="7" cy="12" r="1.6" />
  </Icon>
);

export const BellIcon = () => (
  <Icon>
    <path d="M6 9a6 6 0 0 1 12 0c0 5 2 6.5 2 6.5H4S6 14 6 9" />
    <path d="M10 19a2 2 0 0 0 4 0" />
  </Icon>
);
