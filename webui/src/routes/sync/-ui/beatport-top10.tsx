/**
 * The three top-10 lists (beatport-ui.js 1605-1853).
 *
 * They look like three copies of one list and differ in every interesting way:
 *
 *  - The two TRACK lists come from ONE endpoint and are populated together, so
 *    they load and fail as a unit — showTop10ListsError writes the SAME error
 *    block into BOTH containers (1742-1755).
 *  - The track lists clean their text with cleanTrackText; the RELEASES list
 *    cleans nothing (1807-1809). Same file, same shape of data.
 *  - The track cards have no click handler of their own. The whole CONTAINER is
 *    clickable, and that handler lives in sync-services.js — see
 *    openBeatportTop10List. The releases list is the other way round: per-card
 *    handlers, no container handler.
 *  - The releases card paints an upscaled 500x500 background as an INLINE style
 *    with a baked gradient (1824-1827), where every slider uses a CSS custom
 *    property.
 *
 * A successful-but-EMPTY response is not an error for any of the three: the
 * populate functions bail (1656, 1700, 1789) and the container keeps its static
 * 'Loading …' markup. That markup is reproduced here, because in the port there
 * is no page-level placeholder left behind to keep.
 */

import type { CSSProperties } from 'react';

import type { BeatportRelease, BeatportTop10Track } from '../-beatport.api';
import type { BeatportDownloadEnv } from '../-beatport.downloads';

import { beatportCardBackground, cleanTrackText } from '../-beatport.core';
import { openBeatportRelease, openBeatportTop10List } from '../-beatport.downloads';
import { loadBeatportTop10Lists, loadBeatportTop10Releases } from '../-beatport.loaders';
import { useBeatportOnce } from '../-beatport.use-section';

/* ── The two track lists ──────────────────────────────────────────────────── */

/** 1654-1737. The two differ only in class prefix, copy and placeholder icon. */
const TRACK_LIST_VARIANTS = {
  beatport: {
    slug: 'top10',
    title: '🎵 Beatport Top 10',
    // 1662. index.html's static copy is the same for this list…
    subtitle: 'Most popular tracks on Beatport',
    placeholderIcon: '🎵',
    loadingTitle: '🎵 Loading Beatport Top 10...',
    loadingSubtitle: 'Fetching trending tracks',
    /** The name the download is filed under (sync-services.js 4897). */
    chartName: 'Beatport Top 10',
  },
  hype: {
    slug: 'hype10',
    title: '🔥 Hype Top 10',
    // 1706. …but NOT for this one: index.html says "Editor's hottest trending
    // picks" and the loaded state says "Editor's trending picks", so the
    // subtitle visibly changes when the data lands. The loaded string is the
    // one to keep — it is what a user sees for all but the first moment.
    subtitle: "Editor's trending picks",
    placeholderIcon: '🔥',
    loadingTitle: '🔥 Loading Hype Top 10...',
    loadingSubtitle: "Fetching editor's picks",
    chartName: 'Hype Top 10',
  },
} as const;

type TrackListVariant = keyof typeof TRACK_LIST_VARIANTS;

function TrackCard({
  track,
  index,
  slug,
  placeholderIcon,
}: {
  track: BeatportTop10Track;
  index: number;
  slug: string;
  placeholderIcon: string;
}) {
  // 1669-1671: cleaned before injection. The container click then scrapes these
  // rendered strings back out in the vanilla, which is why the cleaning has to
  // happen here and not only on the way to the download.
  const title = cleanTrackText(track.title || 'Unknown Title');
  return (
    <div className={`beatport-${slug}-card`} data-url={track.url || '#'}>
      {/* 1675: the API's own rank wins; the position is only the fallback, so a
          list the backend ranked out of order keeps its numbers. */}
      <div className={`beatport-${slug}-card-rank`}>{track.rank || index + 1}</div>
      <div className={`beatport-${slug}-card-artwork`}>
        {track.artwork_url ? (
          <img src={track.artwork_url} alt={title} loading="lazy" />
        ) : (
          <div className={`beatport-${slug}-card-placeholder`}>{placeholderIcon}</div>
        )}
      </div>
      <div className={`beatport-${slug}-card-info`}>
        <h4 className={`beatport-${slug}-card-title`}>{title}</h4>
        <p className={`beatport-${slug}-card-artist`}>
          {cleanTrackText(track.artist || 'Unknown Artist')}
        </p>
        <p className={`beatport-${slug}-card-label`}>
          {cleanTrackText(track.label || 'Unknown Label')}
        </p>
      </div>
    </div>
  );
}

export interface TrackTop10ListProps {
  variant: TrackListVariant;
  tracks: BeatportTop10Track[];
  env: BeatportDownloadEnv;
  /**
   * The genre page reuses these lists verbatim — same classes, same cards, same
   * container-level click — and changes only the element id, the subtitle and
   * the name the download is filed under (3181-3226). So the overrides are
   * exactly those three, and everything else stays shared.
   */
  listId?: string;
  subtitle?: string;
  chartName?: string;
}

export function TrackTop10List({
  variant,
  tracks,
  env,
  listId,
  subtitle,
  chartName,
}: TrackTop10ListProps) {
  const copy = TRACK_LIST_VARIANTS[variant];
  return (
    <div
      className={`beatport-${copy.slug}-list`}
      id={listId ?? `beatport-${copy.slug}-list`}
      // sync-services.js 3948-3963: the CONTAINER is the button. Clicking the
      // header queues all ten tracks, which is unobvious but is the behaviour.
      onClick={() => {
        void openBeatportTop10List(tracks, chartName ?? copy.chartName, env);
      }}
    >
      <div className={`beatport-${copy.slug}-list-header`}>
        <h3 className={`beatport-${copy.slug}-list-title`}>{copy.title}</h3>
        <p className={`beatport-${copy.slug}-list-subtitle`}>{subtitle ?? copy.subtitle}</p>
      </div>
      {tracks.length > 0 ? (
        <div className={`beatport-${copy.slug}-tracks`}>
          {tracks.map((track, index) => (
            <TrackCard
              key={index}
              track={track}
              index={index}
              slug={copy.slug}
              placeholderIcon={copy.placeholderIcon}
            />
          ))}
        </div>
      ) : (
        // The vanilla leaves index.html's placeholder in place; the port has to
        // render it, because there is nothing underneath to leave.
        <div className={`beatport-${copy.slug}-tracks`} id={`beatport-${copy.slug}-tracks`}>
          <div className={`beatport-${copy.slug}-loading`}>
            <div className={`beatport-${copy.slug}-loading-content`}>
              <h4>{copy.loadingTitle}</h4>
              <p>{copy.loadingSubtitle}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Both track lists, loaded together because one endpoint feeds both — and
 * failing together for the same reason.
 */
export function BeatportTop10Lists({ env }: { env: BeatportDownloadEnv }) {
  const { data, errorMessage } = useBeatportOnce('beatport:top10-lists', loadBeatportTop10Lists);

  if (errorMessage !== null) {
    // 1753-1754: the SAME block into both containers, and it replaces the whole
    // container — so the list headers go with it.
    return (
      <div className="beatport-top10-container">
        <Top10ListsError message={errorMessage} />
        <Top10ListsError message={errorMessage} />
      </div>
    );
  }

  return (
    <div className="beatport-top10-container">
      <TrackTop10List variant="beatport" tracks={data?.beatport ?? []} env={env} />
      <TrackTop10List variant="hype" tracks={data?.hype ?? []} env={env} />
    </div>
  );
}

/** 1746-1751 — one class for both lists, not a per-list one. */
function Top10ListsError({ message }: { message: string }) {
  return (
    <div className="beatport-top10-error">
      <h3>❌ Error Loading Data</h3>
      <p>{message}</p>
    </div>
  );
}

/* ── Top 10 releases ──────────────────────────────────────────────────────── */

/**
 * 1796-1830. Note what is NOT here: no cleanTrackText anywhere, where the two
 * track lists clean all three text fields.
 */
export function ReleaseTop10Card({
  release,
  index,
  onClick,
}: {
  release: BeatportRelease;
  index: number;
  onClick: () => void;
}) {
  const image = release.image_url || '';
  return (
    <div
      className="beatport-releases-top10-card"
      data-url={release.url || '#'}
      data-bg-image={image}
      // 1824-1827: an inline background with the gradient baked in and the
      // artwork upscaled 95x95 -> 500x500 — the only card in the file that does
      // not use a CSS custom property. Applied only when there is an image, as
      // the vanilla's `if (bgImage)` requires.
      style={
        image
          ? ({
              backgroundImage: beatportCardBackground(image),
              backgroundSize: 'cover',
              backgroundPosition: 'center',
            } as CSSProperties)
          : undefined
      }
      onClick={onClick}
    >
      <div className="beatport-releases-top10-card-rank">{release.rank || index + 1}</div>
      <div className="beatport-releases-top10-card-artwork">
        {release.image_url ? (
          // The THUMBNAIL keeps the original 95px url; only the background is
          // upscaled.
          <img src={release.image_url} alt={release.title} loading="lazy" />
        ) : (
          <div className="beatport-releases-top10-card-placeholder">💿</div>
        )}
      </div>
      <div className="beatport-releases-top10-card-info">
        <h4 className="beatport-releases-top10-card-title">{release.title || 'Unknown Title'}</h4>
        <p className="beatport-releases-top10-card-artist">{release.artist || 'Unknown Artist'}</p>
        <p className="beatport-releases-top10-card-label">{release.label || 'Unknown Label'}</p>
      </div>
    </div>
  );
}

export function BeatportTop10Releases({ env }: { env: BeatportDownloadEnv }) {
  const { data, errorMessage } = useBeatportOnce(
    'beatport:top10-releases',
    loadBeatportTop10Releases,
  );

  if (errorMessage !== null) {
    // 1842-1852 — its own error class and its own title, unlike the shared one
    // the two track lists use.
    return (
      <div className="beatport-releases-top10-list" id="beatport-releases-top10-list">
        <div className="beatport-releases-top10-error">
          <h3>❌ Error Loading Releases</h3>
          <p>{errorMessage}</p>
        </div>
      </div>
    );
  }

  const releases = data ?? [];
  return (
    <div className="beatport-releases-top10-list" id="beatport-releases-top10-list">
      {releases.length > 0 ? (
        <div className="beatport-releases-top10-tracks">
          {releases.map((release, index) => (
            <ReleaseTop10Card
              key={index}
              release={release}
              index={index}
              // 1834 wires EVERY card, with no url test — so an url-less
              // release reaches the handler and shows its toast. This is the
              // one list where that toast is reachable.
              onClick={() => {
                void openBeatportRelease(release, env);
              }}
            />
          ))}
        </div>
      ) : (
        <div className="beatport-releases-top10-loading">
          <div className="beatport-releases-top10-loading-content">
            <h4>💿 Loading Top 10 Releases...</h4>
            <p>Fetching trending albums and EPs</p>
          </div>
        </div>
      )}
    </div>
  );
}
