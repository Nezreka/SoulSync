import { useQuery } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

import styles from './media-server-badges.module.css';

/** #2213: which media server recognised an artist, album or track, shown with
 *  that server's own logo and a green tick. the recognition itself comes from
 *  the backend's `media_server_sources`; this file only draws it. */

interface ServerBrand {
  label: string;
  /** a logo the app already ships; none draws a plain server glyph */
  logo?: string;
}

const SERVER_BRANDS: Record<string, ServerBrand> = {
  plex: { label: 'Plex', logo: '/static/img/brands/plex.png' },
  jellyfin: { label: 'Jellyfin', logo: '/static/img/brands/jellyfin.png' },
  navidrome: { label: 'Navidrome', logo: '/static/img/brands/navidrome.png' },
  music_assistant: { label: 'Music Assistant', logo: '/static/img/brands/music-assistant.png' },
  soulsync: { label: 'SoulSync', logo: '/static/trans2.png' },
  emby: { label: 'Emby' },
};

function serverKey(source: string): string {
  return source.trim().toLowerCase().replace(/-/g, '_');
}

export function mediaServerLabel(source: string): string {
  const key = serverKey(source);
  return (
    SERVER_BRANDS[key]?.label ??
    key
      .split(/_+/)
      .filter(Boolean)
      .map((part) => `${part[0]?.toUpperCase() ?? ''}${part.slice(1)}`)
      .join(' ')
  );
}

function naturalList(values: string[]): string {
  if (values.length <= 1) return values[0] ?? '';
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(', ')}, and ${values.at(-1)}`;
}

interface MediaServerDescriptor {
  name: string;
  configured?: boolean;
}

/** name -> configured, from the server descriptors. probe=0 reads config only,
 *  it never asks a server whether it is up. */
function useConfiguredMediaServers(): Map<string, boolean> | null {
  const query = useQuery({
    queryKey: ['media-servers', 'configured'],
    queryFn: () =>
      readJson<{ servers?: MediaServerDescriptor[] }>(
        apiClient.get('media-servers', { searchParams: { probe: '0' } }),
      ),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const servers = query.data?.servers;
  if (!servers?.length) return null;
  return new Map(servers.map((server) => [serverKey(server.name), server.configured !== false]));
}

/** the sources worth a badge: deduped, and never one for a server that is no
 *  longer set up. while the descriptors are loading, or for a server they
 *  don't list, the recognition shows as it always did. */
function visibleSources(
  sources: string[] | undefined,
  configured: Map<string, boolean> | null,
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of sources ?? []) {
    const key = serverKey(raw);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    if (configured?.get(key) === false) continue;
    out.push(key);
  }
  return out;
}

function ServerLogo({ server }: { server: string }) {
  const logo = SERVER_BRANDS[server]?.logo;
  return (
    <span className={styles.logo} data-server={server} aria-hidden="true">
      {logo ? (
        <img src={appURL(logo)} alt="" draggable={false} />
      ) : (
        <svg viewBox="0 0 24 24">
          <rect x="4" y="4" width="16" height="6.5" rx="1.5" />
          <rect x="4" y="13.5" width="16" height="6.5" rx="1.5" />
        </svg>
      )}
    </span>
  );
}

function Tick() {
  return (
    <svg className={styles.tick} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}

export type MediaServerBadgeVariant = 'card' | 'header' | 'compact';

/** `card`: one dark chip per server over the artwork, logo then tick.
 *  `header`: the artist header's fact pills, logo, name and tick.
 *  `compact`: table rows, the logos together and one tick. */
export function MediaServerRecognitionBadges({
  sources,
  variant,
  className,
}: {
  sources: string[] | undefined;
  variant: MediaServerBadgeVariant;
  className?: string;
}) {
  const configured = useConfiguredMediaServers();
  const servers = visibleSources(sources, configured);
  if (servers.length === 0) return null;

  if (variant === 'compact') {
    const description = `Recognised by ${naturalList(servers.map(mediaServerLabel))}`;
    return (
      <span
        role="img"
        className={`${styles.compact} ${className ?? ''}`}
        aria-label={description}
        title={description}
        data-media-server-badges="compact"
      >
        {servers.map((server) => (
          <ServerLogo key={server} server={server} />
        ))}
        <Tick />
      </span>
    );
  }

  const chips = servers.map((server) => {
    const label = mediaServerLabel(server);
    const description = `Recognised by ${label}`;
    return (
      <span
        key={server}
        role="img"
        className={variant === 'card' ? styles.chip : styles.pill}
        aria-label={description}
        title={description}
        data-server={server}
      >
        <ServerLogo server={server} />
        {variant === 'header' ? (
          <span className={styles.name} aria-hidden="true">
            {label}
          </span>
        ) : null}
        <Tick />
      </span>
    );
  });

  if (variant === 'header') return <>{chips}</>;
  return (
    <span className={`${styles.cardGroup} ${className ?? ''}`} data-media-server-badges="card">
      {chips}
    </span>
  );
}
