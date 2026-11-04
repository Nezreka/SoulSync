/** The library page's stroke icons, for the buttons that used emoji. Same
 *  paths as the page's own icon set, so a toolbar reads as one family. */
const PATHS = {
  radio: 'M4 10h16v10H4zM7 10l10-5M8.5 15h.01M13 14h4M13 17h4',
  download: 'M12 3v12M8 11l4 4 4-4M5 21h14',
  filter: 'M4 6h16M7 12h10M10 18h4',
} as const;

export function LineIcon({ name, size = 15 }: { name: keyof typeof PATHS; size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flexShrink: 0, verticalAlign: '-2px' }}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
