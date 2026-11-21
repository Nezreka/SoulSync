/** the playback controls' icons, on the player's icon grid */

import { Icon } from '../icons';

export const RepeatIcon = () => (
  <Icon>
    <path d="M17 2l3 3-3 3" />
    <path d="M4 11V9a4 4 0 0 1 4-4h12" />
    <path d="M7 22l-3-3 3-3" />
    <path d="M20 13v2a4 4 0 0 1-4 4H4" />
  </Icon>
);

export const RepeatOneIcon = () => (
  <Icon>
    <path d="M17 2l3 3-3 3" />
    <path d="M4 11V9a4 4 0 0 1 4-4h12" />
    <path d="M7 22l-3-3 3-3" />
    <path d="M20 13v2a4 4 0 0 1-4 4H4" />
    <path d="M11.5 10.5 13 9.5V15" strokeWidth={1.6} />
  </Icon>
);

export const VolumeIcon = ({ level }: { level: 0 | 1 | 2 }) => (
  <Icon>
    <path d="M4 9.5h3.5L12 5v14l-4.5-4.5H4z" fill="currentColor" />
    {level === 0 ? <path d="m16 9.5 5 5M21 9.5l-5 5" /> : null}
    {level >= 1 ? <path d="M15.5 9a4 4 0 0 1 0 6" /> : null}
    {level === 2 ? <path d="M18 6.5a7.5 7.5 0 0 1 0 11" /> : null}
  </Icon>
);

export const QueueIcon = () => (
  <Icon>
    <path d="M4 6h12M4 12h12M4 18h8" />
    <path d="M17 15v6l4-3z" fill="currentColor" />
  </Icon>
);

export const BackSecondsIcon = ({ n }: { n: number }) => (
  <Icon>
    <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" />
    <path d="M4 3.5v3.7h3.7" />
    <text
      x="12"
      y="15.2"
      textAnchor="middle"
      fontSize="6.6"
      fontWeight="700"
      fill="currentColor"
      stroke="none"
    >
      {n}
    </text>
  </Icon>
);

export const ForwardSecondsIcon = ({ n }: { n: number }) => (
  <Icon>
    <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
    <path d="M20 3.5v3.7h-3.7" />
    <text
      x="12"
      y="15.2"
      textAnchor="middle"
      fontSize="6.6"
      fontWeight="700"
      fill="currentColor"
      stroke="none"
    >
      {n}
    </text>
  </Icon>
);

export const SleepIcon = () => (
  <Icon>
    <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" />
  </Icon>
);

export const ChaptersIcon = () => (
  <Icon>
    <path d="M5 5h14M5 10h14M5 15h9M5 20h6" />
  </Icon>
);

export const ExpandIcon = () => (
  <Icon>
    <path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" />
  </Icon>
);

export const CollapseIcon = () => (
  <Icon>
    <path d="M4 14h6v6M20 10h-6V4M10 14l-7 7M14 10l7-7" />
  </Icon>
);

export const FullscreenIcon = ({ on }: { on: boolean }) =>
  on ? (
    <Icon>
      <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />
    </Icon>
  ) : (
    <Icon>
      <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
    </Icon>
  );

export const PipIcon = () => (
  <Icon>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <rect x="12" y="12" width="7" height="5" rx="1" fill="currentColor" />
  </Icon>
);

export const CloseIcon = () => (
  <Icon>
    <path d="M6 6l12 12M18 6 6 18" />
  </Icon>
);

export const SpeedIcon = () => (
  <Icon>
    <path d="M12 20a8 8 0 1 1 8-8" />
    <path d="m12 12 4-3" />
  </Icon>
);

export const QualityIcon = () => (
  <Icon>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path
      d="M7.5 9.5v5M7.5 12h3M10.5 9.5v5M14 9.5h2a2 2 0 0 1 2 2v1a2 2 0 0 1-2 2h-2z"
      strokeWidth={1.5}
    />
  </Icon>
);

export const AudioIcon = () => (
  <Icon>
    <path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 11v2" />
  </Icon>
);

export const SubtitlesIcon = () => (
  <Icon>
    <rect x="3" y="5" width="18" height="14" rx="2.5" />
    <path d="M7 13h4M13 13h4M7 16h7" />
  </Icon>
);

export const StatsIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5.5M12 7.5v.01" />
  </Icon>
);
