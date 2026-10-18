import { useState } from 'react';

import { Button } from '@/components/form/form';
import { serviceName } from '@/components/provider-reference/provider-names';
import { copyRecordText } from '@/components/record-inspector/record-helpers';

import { musicBrainzBrowserUrl } from './musicbrainz-links';
import styles from './provider-reference.module.css';

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
  const provider = serviceName(source.toLowerCase(), source);
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
  const [copyState, setCopyState] = useState('');
  return (
    <span className={styles.providerReference}>
      <code>{id}</code>
      <Button
        size="icon"
        variant="ghost"
        aria-label={`Copy ${provider} identifier`}
        onClick={() => {
          void copyRecordText(id)
            .then(() => setCopyState('Identifier copied'))
            .catch(() => setCopyState('Could not copy identifier'));
        }}
      >
        ⧉
      </Button>
      {copyState && <small role="status">{copyState}</small>}
      {href && (
        <a href={href} target="_blank" rel="noreferrer">
          View on {provider} ↗
        </a>
      )}
    </span>
  );
}
