import { musicBrainzBrowserUrl } from './musicbrainz-links';

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
  return href ? (
    <a href={href} target="_blank" rel="noreferrer">
      <code>{releaseId}</code>
    </a>
  ) : (
    <code>{releaseId}</code>
  );
}
