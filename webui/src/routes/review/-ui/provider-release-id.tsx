import { musicBrainzBrowserUrl } from './musicbrainz-links';
import styles from './review-page.module.css';

function releaseUrl(source: string, releaseId: string): string | null {
  switch (source.toLowerCase()) {
    case 'musicbrainz':
      return musicBrainzBrowserUrl('release', releaseId);
    case 'itunes':
      return `https://music.apple.com/us/album/${encodeURIComponent(releaseId)}`;
    case 'deezer':
      return `https://www.deezer.com/album/${encodeURIComponent(releaseId)}`;
    default:
      return null;
  }
}

export function ProviderReleaseId({
  source = '',
  releaseId = '',
}: {
  source?: string;
  releaseId?: string;
}) {
  if (!releaseId) return <code>unknown</code>;
  const href = releaseUrl(source, releaseId);
  const provider =
    { musicbrainz: 'MusicBrainz', itunes: 'Apple Music', deezer: 'Deezer' }[source.toLowerCase()] ||
    source;
  return <ProviderReference id={releaseId} href={href} provider={provider} />;
}

export function ProviderReference({
  id,
  href,
  provider,
}: {
  id: string;
  href: string | null;
  provider: string;
}) {
  return (
    <span className={styles.providerReference}>
      <code>{id}</code>
      {href && (
        <a href={href} target="_blank" rel="noreferrer">
          View on {provider} ↗
        </a>
      )}
    </span>
  );
}
