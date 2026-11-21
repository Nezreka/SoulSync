/** the settings page's line icons (the mockups' sprite): one stroke weight,
 *  drawn in the text colour */

import type { ReactNode } from 'react';

const PATHS = {
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ),
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  copy: (
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3" />
    </>
  ),
  chev: <path d="m9 6 6 6-6 6" />,
  down: <path d="m6 9 6 6 6-6" />,
  up: <path d="m6 15 6-6 6 6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  gear: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </>
  ),
  warn: (
    <>
      <path d="M12 4 2.8 19.5h18.4L12 4z" />
      <path d="M12 10v4.5M12 17.3v.2" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5v.2" />
    </>
  ),
  link: (
    <>
      <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
      <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
    </>
  ),
  dl: <path d="M12 4v11M7 11l5 5 5-5M5 20h14" />,
  sliders: (
    <>
      <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="10" cy="17" r="2" />
    </>
  ),
  lib: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="2.5" />
      <path d="M9 4v16" />
    </>
  ),
  compass: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m15.5 8.5-2 5-5 2 2-5 5-2z" />
    </>
  ),
  people: (
    <>
      <circle cx="9" cy="9" r="3.2" />
      <path d="M3 19c.5-3.2 3-5 6-5s5.5 1.8 6 5" />
      <circle cx="17" cy="8" r="2.5" />
      <path d="M16.5 13.5c2.5.2 4 1.8 4.5 4.5" />
    </>
  ),
  palette: (
    <>
      <path d="M12 3a9 9 0 1 0 0 18c1.5 0 2-1 2-2s-.5-1.5-.5-2.5S14.5 15 16 15h2a3 3 0 0 0 3-3c0-5-4-9-9-9z" />
      <circle cx="8" cy="11" r="1" />
      <circle cx="11.5" cy="7.5" r="1" />
      <circle cx="15.5" cy="9" r="1" />
    </>
  ),
  plug: <path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0V8zM12 17v4" />,
  folder: (
    <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v7.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5v-10z" />
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="9.5" rx="2" />
      <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
    </>
  ),
  unlock: (
    <>
      <rect x="5" y="10.5" width="14" height="9.5" rx="2" />
      <path d="M8 10.5V8a4 4 0 0 1 7.6-1.7" />
    </>
  ),
  eye: (
    <>
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
      <circle cx="12" cy="12" r="2.8" />
    </>
  ),
  eyeOff: (
    <>
      <path d="M4 4l16 16M9.9 5.8A8.7 8.7 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.500a17 17 0 0 1-3 3.800M6.3 7.700A16 16 0 0 0 2.5 12S6 18.5 12 18.500a8.6 8.6 0 0 0 3.6-.8" />
      <path d="M9.9 9.900a2.8 2.8 0 0 0 4 4" />
    </>
  ),
  refresh: (
    <path d="M20 11a8 8 0 0 0-14.5-4.500L4 8M4 4v4h4M4 13a8 8 0 0 0 14.5 4.500L20 16M20 20v-4h-4" />
  ),
  magnet: <path d="M5 4h4v8a3 3 0 0 0 6 0V4h4v8a7 7 0 0 1-14 0V4zM5 8h4M15 8h4" />,
  news: (
    <>
      <rect x="3.5" y="5" width="17" height="14" rx="2" />
      <path d="M7 9h5v4H7zM15 9h2.500M15 13h2.500M7 16h10.5" />
    </>
  ),
  box: <path d="m12 3 8 4.500v9L12 21l-8-4.500v-9L12 3zM4 7.500l8 4.5 8-4.500M12 12v9" />,
  wave: <path d="M4 10v4M8 6v12M12 9v6M16 4v16M20 10v4" />,
  undo: (
    <>
      <path d="M8 5 4 9l4 4" />
      <path d="M4 9h10a5.5 5.5 0 0 1 0 11h-3" />
    </>
  ),
  phone: (
    <>
      <rect x="7" y="2.5" width="10" height="19" rx="2.2" />
      <path d="M11 18.5h2" />
    </>
  ),
  logs: (
    <>
      <path d="M6 3.500h8l4 4V20.500H6z" />
      <path d="M9 12h6M9 16h6" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof PATHS | 'grip' | 'more';

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  // the two dotted glyphs are filled, not stroked
  if (name === 'grip' || name === 'more') {
    const dots =
      name === 'grip'
        ? [
            [9, 6],
            [15, 6],
            [9, 12],
            [15, 12],
            [9, 18],
            [15, 18],
          ]
        : [
            [5, 12],
            [12, 12],
            [19, 12],
          ];
    return (
      <svg
        viewBox="0 0 24 24"
        width={size}
        height={size}
        fill="currentColor"
        aria-hidden="true"
        focusable="false"
      >
        {dots.map(([cx, cy]) => (
          <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={name === 'grip' ? 1.3 : 1.5} />
        ))}
      </svg>
    );
  }
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
