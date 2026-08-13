import { useEffect, useRef } from 'react';

import type { ArtMapNode } from '../-discover.artist-map';
import type { ArtMapArtistCard, ArtMapPanelModel, MiniStat } from '../-discover.artist-map.panel';

import { artMap } from '../-discover.artist-map';
import { artMapCoverageGradient } from '../-discover.artist-map.panel';

/**
 * The Artist Map's info panel.
 *
 * Two bodies behind one header: a dashboard with the current scope's coverage
 * and a top-artists list, or a rich card for whichever bubble you settled on.
 *
 * Transcribed from `webui/static/discover.js` 6313-6521. The numbers, labels and
 * ordering all come from `-discover.artist-map.panel`, which is differentially
 * tested against the vanilla — this file only renders them.
 */

function Stat({ stat }: { stat: MiniStat }) {
  return (
    <div className="artmap-ministat">
      <div className="artmap-ministat-value" style={{ color: stat.color }}>
        {stat.value}
      </div>
      <div className="artmap-ministat-label">{stat.label}</div>
    </div>
  );
}

/**
 * The cached bitmap, painted into a canvas.
 *
 * Preferred over the raw URL because it is already decoded and circle-masked, so
 * it appears instantly and cannot churn-blank the way a fresh `<img src>` does
 * while sweeping across dense bubbles.
 */
function CardAvatar({ card }: { card: ArtMapArtistCard }) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (!card.hasBitmap || !ref.current) return;
    const bmp = artMap.images[card.id as string];
    if (!bmp) return;
    try {
      ref.current.getContext('2d')?.drawImage(bmp, 0, 0, 120, 120);
    } catch {
      /* a tainted or closed bitmap is not worth failing a render over */
    }
  }, [card.id, card.hasBitmap]);

  if (card.hasBitmap) {
    return <canvas ref={ref} className="artmap-card-canvas" width={120} height={120} />;
  }
  if (card.imageUrl) {
    return (
      <img
        src={card.imageUrl}
        alt=""
        className="artmap-card-img"
        onError={(e) => {
          e.currentTarget.style.display = 'none';
        }}
      />
    );
  }
  return <span className="artmap-card-glyph">♫</span>;
}

export interface ArtMapPanelProps {
  model: ArtMapPanelModel;
  /** The card to show instead of the top list, or null for the dashboard. */
  card: ArtMapArtistCard | null;
  isMobile: boolean;
  /** Only meaningful on mobile, where the panel is a bottom sheet. */
  open: boolean;
  onSelectArtist: (id: ArtMapNode['id']) => void;
  onBackToList: () => void;
  onCloseSheet: () => void;
  onToggleWatch: (id: ArtMapNode['id']) => void;
  onExplore: (name: string) => void;
  onOpenDetails: (id: ArtMapNode['id']) => void;
  buildArtistDetailPath: (id: string, source: string) => string;
}

export function ArtMapPanel({
  model,
  card,
  isMobile,
  open,
  onSelectArtist,
  onBackToList,
  onCloseSheet,
  onToggleWatch,
  onExplore,
  onOpenDetails,
  buildArtistDetailPath,
}: ArtMapPanelProps) {
  const [from, to] = artMapCoverageGradient(model.hue);

  return (
    <div
      id="artmap-info-panel"
      className={isMobile ? 'artmap-panel artmap-panel--sheet' : 'artmap-panel'}
      // The sheet slides fully off screen when closed; the desktop sidebar never
      // transforms (6334 / 6347).
      style={isMobile ? { transform: `translateY(${open ? '0' : '100%'})` } : undefined}
    >
      {isMobile && (
        <button
          type="button"
          id="artmap-panel-grip"
          className="artmap-panel-grip"
          aria-label="Close panel"
          onClick={onCloseSheet}
        >
          <span />
        </button>
      )}

      <div id="artmap-panel-head" className="artmap-panel-head">
        <div className="artmap-panel-eyebrow">{model.title}</div>
        <div
          className="artmap-panel-heading"
          style={model.island ? { color: `hsl(${model.hue},82%,80%)` } : undefined}
        >
          {model.island ? model.island.name : 'Overview'}
        </div>
        <div className="artmap-panel-stats">
          {model.stats.map((stat) => (
            <Stat key={stat.label} stat={stat} />
          ))}
        </div>
        <div className="artmap-panel-coverage">
          <div className="artmap-panel-coverage-row">
            <span>On your watchlist</span>
            <span>
              {model.scopeWatch}/{model.scopeTotal}
            </span>
          </div>
          <div className="artmap-panel-coverage-track">
            <div
              className="artmap-panel-coverage-fill"
              style={{
                width: `${model.coveragePct}%`,
                background: `linear-gradient(90deg,${from},${to})`,
              }}
            />
          </div>
        </div>
      </div>

      <div id="artmap-panel-body" className="artmap-panel-body">
        {card ? (
          <>
            <button type="button" className="artmap-card-back" onClick={onBackToList}>
              ← Top artists
            </button>
            <div className="artmap-card-head">
              <div
                className="artmap-card-avatar"
                style={{
                  borderColor: `hsl(${card.hue},80%,65%)`,
                  boxShadow: `0 8px 28px hsla(${card.hue},80%,40%,0.4)`,
                }}
              >
                <CardAvatar card={card} />
              </div>
              <div className="artmap-card-name">{card.name}</div>
              <div className="artmap-card-type" style={{ color: `hsl(${card.hue},70%,72%)` }}>
                {card.typeLabel}
              </div>
            </div>

            {card.genres.length > 0 && (
              <div className="artmap-card-genres">
                {card.genres.map((g) => (
                  <span
                    key={g}
                    className="artmap-card-genre"
                    style={{
                      background: `hsla(${card.hue},60%,55%,0.16)`,
                      borderColor: `hsla(${card.hue},60%,60%,0.3)`,
                      color: `hsl(${card.hue},70%,82%)`,
                    }}
                  >
                    {g}
                  </span>
                ))}
              </div>
            )}

            <div className="artmap-panel-stats">
              <Stat
                stat={{
                  label: 'Popularity',
                  value: card.popularity,
                  color: `hsl(${card.hue},80%,80%)`,
                }}
              />
              <Stat stat={{ label: 'Connections', value: card.connections, color: '#fff' }} />
            </div>
            <div className="artmap-panel-coverage-track artmap-card-pop">
              <div
                className="artmap-panel-coverage-fill"
                style={{
                  width: `${card.popularity}%`,
                  background: `linear-gradient(90deg,hsl(${card.hue},80%,60%),hsl(${(card.hue + 40) % 360},80%,62%))`,
                }}
              />
            </div>

            <div className="artmap-card-actions">
              <button
                type="button"
                className="artmap-card-explore"
                style={{
                  background: `linear-gradient(90deg,hsl(${card.hue},70%,52%),hsl(${(card.hue + 30) % 360},70%,54%))`,
                }}
                onClick={() => onExplore(card.name)}
              >
                Explore from here →
              </button>
              <div className="artmap-card-action-row">
                <button
                  type="button"
                  className="artmap-card-details"
                  onClick={() => onOpenDetails(card.id)}
                >
                  Details
                </button>
                <button
                  type="button"
                  id="artmap-card-watch"
                  className="artmap-card-watch"
                  style={{
                    background: card.watch.background,
                    borderColor: card.watch.borderColor,
                  }}
                  onClick={() => onToggleWatch(card.id)}
                >
                  {card.watch.label}
                </button>
              </div>
              {card.best.id && (
                <a
                  className="artmap-card-open"
                  href={buildArtistDetailPath(card.best.id, card.best.source)}
                >
                  Open artist page
                </a>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="artmap-panel-eyebrow artmap-panel-list-title">Top artists</div>
            {model.topArtists.length === 0 ? (
              <div className="artmap-panel-empty">No artists</div>
            ) : (
              model.topArtists.map((n, i) => (
                <button
                  type="button"
                  key={String(n.id)}
                  className="artmap-panel-row"
                  onClick={() => onSelectArtist(n.id)}
                >
                  <span className="artmap-panel-rank">{i + 1}</span>
                  <span className="artmap-panel-thumb">
                    {n.image_url ? (
                      <img
                        src={n.image_url}
                        alt=""
                        loading="lazy"
                        onError={(e) => {
                          e.currentTarget.style.display = 'none';
                        }}
                      />
                    ) : (
                      '♫'
                    )}
                  </span>
                  <span className="artmap-panel-row-name">{n.name}</span>
                  {n.type === 'watchlist' && <span className="artmap-panel-star">★</span>}
                </button>
              ))
            )}
          </>
        )}
      </div>
    </div>
  );
}
