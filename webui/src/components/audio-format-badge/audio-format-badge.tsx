import styles from './audio-format-badge.module.css';

/** The Library's compact format label, shared with local audio inspection. */
export function AudioFormatBadge({ format }: { format: string }) {
  const label = format.toUpperCase();
  return (
    <span
      className={`${styles.badge} enhanced-format-badge ${label === 'FLAC' ? 'flac' : label === 'MP3' ? 'mp3' : 'other'}`}
    >
      {label}
    </span>
  );
}
