/** one line icon per feature, same stroke style as the sidebar */

const PATHS: Record<string, string> = {
  discover: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM15.5 8.5l-2 5-5 2 2-5z',
  playlists: 'M16 3h5v5M4 20 21 3M21 16v5h-5M15 15l6 6M4 4l5 5',
  explorer: 'M12 3a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM12 7v5M5 12h14M5 12v6M19 12v6M12 12v6',
  import: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  stats: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  music_videos: 'M3 5h13v14H3zM16 10l5-3v10l-5-3',
  sample_studio: 'M3 12h1M7 8v8M11 5v14M15 9v6M19 7v10',
  podcasts:
    'M12 13a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM12 15v6M7.8 15.5a6 6 0 1 1 8.4 0M5 18.5a10 10 0 1 1 14 0',
  audiobooks: 'M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM4 21a2 2 0 0 1 2-2h12v2',
  video: 'M3 5h18v12H3zM8 21h8M12 17v4',
  chat: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  automations: 'M13 2 3 14h9l-1 8 10-12h-9z',
  tools: 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z',
};

export function FeatureIcon({ id }: { id: string }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={PATHS[id] ?? 'M12 12h0'} />
    </svg>
  );
}
