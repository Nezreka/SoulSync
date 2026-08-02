import type {
  WebArtistCard,
  WebDiscoveryCard,
  WebGenreCard,
  WebPathRow,
} from '../-discover.artist-web.panel';

import { miniStat } from '../-discover.artist-map.panel';
import { webHexToRgba, WEB_DISCOVERY_COLOR } from '../-discover.artist-web';
import { artWebPathSummary } from '../-discover.artist-web';
import { WEB_SHORTCUTS } from '../-discover.artist-web.panel';

/**
 * The Artist Web's side panel and guide.
 *
 * Transcribed from discover.js 7770-7793 (path), 7848-7890 (guide),
 * 7893-7916 (the panel shell), 7974-8028 (artist), 8030-8053 (genre) and
 * 8059-8093 (discovery).
 *
 * The vanilla builds this panel by hand and appends it to the container the
 * first time something is selected; here it is a component that is simply not
 * rendered when nothing is. The consequence worth knowing is that closing it
 * must still stop a running preview — the vanilla routes every close through
 * one function for exactly that reason, and a React unmount has to do the same.
 */

const PANEL_STYLE: React.CSSProperties = {
  position: 'absolute',
  top: 66,
  right: 14,
  width: 300,
  maxHeight: 'calc(100% - 88px)',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  zIndex: 20,
};

/**
 * The panel shell.
 *
 * Note what is NOT here: the vanilla is emphatic that the container must not be
 * given `position: relative`, because `.artist-map-container` is `position:
 * fixed; inset: 0` in CSS and an inline `relative` collapses the fullscreen
 * overlay and displaces the sigma canvas, which freezes clicks. Nothing in this
 * component touches the container's position.
 */
export function ArtWebPanel({ children }: { children: React.ReactNode }) {
  return (
    <div id="artweb-panel" className="artweb-panel" style={PANEL_STYLE}>
      <div id="artweb-panel-body" className="artweb-panel-body">
        {children}
      </div>
    </div>
  );
}

function CloseRow({ onClose, label = '✕ Close' }: { onClose: () => void; label?: string }) {
  return (
    <button type="button" className="artweb-panel-close" onClick={onClose}>
      {label}
    </button>
  );
}

// ── The owned-artist card ────────────────────────────────────────────────────

export interface ArtWebArtistCardProps {
  card: WebArtistCard;
  /** The artist's own photo, once it has resolved. */
  imageUrl?: string | null;
  onClose: () => void;
  onPlayRadio: (key: string) => void;
  onExpand: (key: string) => void;
  onExploreInMap: (label: string) => void;
}

export function ArtWebArtistCard({
  card,
  imageUrl,
  onClose,
  onPlayRadio,
  onExpand,
  onExploreInMap,
}: ArtWebArtistCardProps) {
  const pop = miniStat('Popularity', card.popularity, 270);
  const conn = miniStat('Connections', card.connections);
  return (
    <>
      <CloseRow onClose={onClose} />
      <div className="artweb-card-head">
        <div
          id="artweb-avatar"
          className="artweb-avatar"
          style={{
            borderColor: card.color,
            boxShadow: `0 8px 28px ${webHexToRgba(card.color, 0.45)}`,
          }}
        >
          {imageUrl ? (
            <img src={imageUrl} alt="" />
          ) : (
            <span className="artweb-avatar-glyph">♫</span>
          )}
        </div>
        <div className="artweb-card-name">{card.label}</div>
        {card.primaryGenre && (
          <div className="artweb-card-genre" style={{ color: card.color }}>
            {card.primaryGenre}
          </div>
        )}
      </div>

      <div className="artweb-card-stats">
        {[pop, conn].map((s) => (
          <div className="artweb-ministat" key={s.label}>
            <div className="artweb-ministat-value" style={{ color: s.color }}>
              {s.value}
            </div>
            <div className="artweb-ministat-label">{s.label}</div>
          </div>
        ))}
      </div>
      <div className="artweb-card-meter">
        <div style={{ width: `${card.popularity}%`, background: card.color }} />
      </div>

      <div className="artweb-card-actions">
        {card.canPlayRadio && (
          <button
            type="button"
            className="artweb-btn-primary"
            onClick={() => onPlayRadio(card.key)}
          >
            ▶ Play radio
          </button>
        )}
        {card.canExpand && (
          <button
            type="button"
            id="artweb-expand-btn"
            className="artweb-btn-lens"
            style={{ background: card.color }}
            onClick={() => onExpand(card.key)}
          >
            {card.expanded ? 'Expanded ✓' : 'Expand connections ✦'}
          </button>
        )}
        <button
          type="button"
          className={card.canExpand ? 'artweb-btn-ghost' : 'artweb-btn-lens'}
          style={card.canExpand ? undefined : { background: card.color }}
          onClick={() => onExploreInMap(card.label)}
        >
          Explore in Artist Map →
        </button>
        {card.detailPath && (
          <a className="artweb-btn-link" href={card.detailPath}>
            Open artist page (discography)
          </a>
        )}
      </div>
    </>
  );
}

// ── The genre-hub card ───────────────────────────────────────────────────────

export interface ArtWebGenreCardProps {
  card: WebGenreCard;
  onClose: () => void;
  onGoToArtist: (key: string) => void;
}

export function ArtWebGenreCard({ card, onClose, onGoToArtist }: ArtWebGenreCardProps) {
  return (
    <>
      <CloseRow onClose={onClose} />
      <div className="artweb-card-kicker">Genre</div>
      <div className="artweb-card-title" style={{ color: card.color }}>
        {card.genre}
      </div>
      {/* The TOTAL, not the thirty listed — a big genre has to read honestly. */}
      <div className="artweb-card-sub">
        {card.total} artist{card.total === 1 ? '' : 's'} in your library
      </div>
      <div className="artweb-card-kicker artweb-card-kicker-spaced">Top artists</div>
      {card.members.length === 0 ? (
        <div className="artweb-card-empty">No artists</div>
      ) : (
        card.members.map((m, i) => (
          <button
            type="button"
            key={m.key}
            className="artweb-member-row"
            onClick={() => onGoToArtist(m.key)}
          >
            <span className="artweb-member-rank">{i + 1}</span>
            <span className="artweb-member-name">{m.label}</span>
          </button>
        ))
      )}
    </>
  );
}

// ── The discovery-candidate card ─────────────────────────────────────────────

export interface ArtWebDiscoveryCardProps {
  card: WebDiscoveryCard;
  /** The preview button's current label — idle, loading, playing or unavailable. */
  previewLabel: string;
  previewBusy?: boolean;
  onClose: () => void;
  onTogglePreview: (key: string) => void;
  onAddToWatchlist: (key: string) => void;
}

export function ArtWebDiscoveryCard({
  card,
  previewLabel,
  previewBusy,
  onClose,
  onTogglePreview,
  onAddToWatchlist,
}: ArtWebDiscoveryCardProps) {
  const color = WEB_DISCOVERY_COLOR;
  return (
    <>
      <CloseRow onClose={onClose} />
      <div className="artweb-card-head">
        <div
          className="artweb-avatar"
          style={{ borderColor: color, boxShadow: `0 8px 28px ${webHexToRgba(color, 0.45)}` }}
        >
          {card.imageUrl ? (
            <img src={card.imageUrl} alt="" />
          ) : (
            <span className="artweb-avatar-glyph">♫</span>
          )}
        </div>
        <div className="artweb-card-name">{card.label}</div>
        <div className="artweb-card-badge" style={{ color, borderColor: webHexToRgba(color, 0.5) }}>
          Not in your library
        </div>
      </div>

      {card.genres.length > 0 && (
        <div className="artweb-card-pills">
          {card.genres.map((g) => (
            <span
              key={g}
              className="artweb-card-pill"
              style={{
                background: webHexToRgba(color, 0.16),
                borderColor: webHexToRgba(color, 0.3),
              }}
            >
              {g}
            </span>
          ))}
        </div>
      )}

      <div className="artweb-card-actions">
        {card.canPreview && (
          <button
            type="button"
            id="artweb-preview-btn"
            className="artweb-btn-ghost"
            disabled={previewBusy}
            onClick={() => onTogglePreview(card.key)}
          >
            {previewLabel}
          </button>
        )}
        {/*
          Deliberately NO expand button. similar_artists only has rows for
          artists whose similars SoulSync fetched, so expanding an unowned
          candidate always comes back empty — 0 of 176 on real data.
        */}
        <button
          type="button"
          id="artweb-add-btn"
          className="artweb-btn-lens"
          style={{ background: color }}
          onClick={() => onAddToWatchlist(card.key)}
        >
          + Add to watchlist
        </button>
        {card.detailPath && (
          <a className="artweb-btn-link" href={card.detailPath}>
            Open artist page (discography)
          </a>
        )}
      </div>
    </>
  );
}

// ── The path card ────────────────────────────────────────────────────────────

export interface ArtWebPathCardProps {
  rows: WebPathRow[];
  onDone: () => void;
  onCameraTo: (key: string) => void;
}

export function ArtWebPathCard({ rows, onDone, onCameraTo }: ArtWebPathCardProps) {
  const { hops, via } = artWebPathSummary(rows.map((r) => r.key));
  return (
    <>
      <CloseRow onClose={onDone} label="✕ Done" />
      <div className="artweb-card-kicker">Connection path</div>
      <div className="artweb-card-title">
        {hops} hop{hops === 1 ? '' : 's'} apart
      </div>
      {/* The sentence comes from the summary, not a second copy of the rule. */}
      <div className="artweb-card-sub">{via}</div>
      {rows.map((row, i) => (
        <div key={row.key}>
          <button type="button" className="artweb-path-row" onClick={() => onCameraTo(row.key)}>
            <span
              className="artweb-path-dot"
              style={{
                background: row.color,
                boxShadow: `0 0 0 ${row.tag ? '2px rgba(255,255,255,0.5)' : '0'}`,
              }}
            />
            <span className="artweb-path-name" style={{ fontWeight: row.tag ? 800 : 600 }}>
              {row.label}
            </span>
            {row.tag && <span className="artweb-path-tag">{row.tag}</span>}
          </button>
          {i < rows.length - 1 && <div className="artweb-path-link" />}
        </div>
      ))}
    </>
  );
}

// ── The guide ────────────────────────────────────────────────────────────────

/**
 * The guide modal (7848-7890).
 *
 * The vanilla's button TOGGLES — clicking it while the guide is open removes it
 * — so the page holds the open flag and this is only rendered when it is set.
 */
export function ArtWebHelpModal({ onClose }: { onClose: () => void }) {
  return (
    <div
      id="artweb-help-overlay"
      className="modal-overlay"
      style={{ zIndex: 10002 }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="artmap-shortcuts-modal artweb-help-modal">
        <div className="artmap-shortcuts-header">
          <h3>Artist Web — Guide</h3>
          <button type="button" className="watch-all-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="artweb-help-body">
          <div className="artweb-help-section">
            <h4>Three lenses</h4>
            <p>
              <b>Genre</b> — your artists grouped by genre. <b>Communities</b> — clusters found from
              who&apos;s actually similar to whom, named by each cluster&apos;s hub artist.{' '}
              <b>Discovery</b> — your library (blue) wired to unowned similar artists (amber) you
              could add.
            </p>
          </div>
          <div className="artweb-help-section">
            <h4>Explore</h4>
            <p>
              <b>Hover</b> to identify a node · <b>click</b> an artist for details + play. On
              Discovery, click a candidate to add it to your watchlist, or an owned node to grow its
              frontier.
            </p>
          </div>
          <div className="artweb-help-section">
            <h4>Tools</h4>
            <p>
              <b>Path</b> — click two artists to trace how they connect · <b>Size by</b> Popular /
              Links / Influence · <b>Edges</b> — strong connections only · <b>Filter</b> — by genre.
              The <b>legend</b> (bottom-left) decodes the colors.
            </p>
          </div>
          <div className="artmap-shortcuts-grid">
            {WEB_SHORTCUTS.map((s) => (
              <div className="artmap-shortcut" key={s.action}>
                {s.keys.map((k, i) => (
                  <span key={k}>
                    {i > 0 && ' / '}
                    <kbd>{k}</kbd>
                  </span>
                ))}
                <span>{s.action}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── The floating hints ───────────────────────────────────────────────────────

/** The path-mode instruction pill (7803-7817). Its text is already sanitised. */
export function ArtWebPathHint({ html }: { html: string }) {
  return (
    <div
      id="artweb-path-hint"
      className="artweb-hint artweb-path-hint"
      // The hint is composed from constants plus an escaped artist label; the
      // bold markers are the whole point of it, so it is set as HTML.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

/** The one-time first-run pill (7825-7845). */
export function ArtWebFirstRunHint({ fading }: { fading: boolean }) {
  return (
    <div
      id="artweb-firstrun-hint"
      className="artweb-hint artweb-firstrun-hint"
      style={{ opacity: fading ? 0 : 1 }}
    >
      Hover to identify · click an artist to explore · <b>?</b> for the guide
    </div>
  );
}
