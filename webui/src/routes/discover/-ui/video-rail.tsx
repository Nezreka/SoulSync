import { usePromoVideo } from '../-discover.use-promo';
import { PromoBanner } from './promo-banner';

/**
 * a rail of tall 9:16 cards, one artist each, like spotify's vertical video
 * feed. every card's artwork is always drifting; point at one and its music
 * video plays in it (the stage gives a hovered card the video first). with
 * nothing pointed at, the most visible card plays.
 */

export interface RailArtist {
  key: string;
  name: string;
  image: string;
  reason?: string;
  href: string;
}

export interface VideoRailProps {
  title: string;
  subtitle: string;
  artists: RailArtist[];
  videosOn: boolean;
}

function RailCard({ artist, videosOn }: { artist: RailArtist; videosOn: boolean }) {
  const promo = usePromoVideo(`rail-${artist.key}`, artist.name, null, artist.image, videosOn);
  return (
    <PromoBanner
      kind="rail"
      size="portrait"
      eyebrow={artist.reason || 'For you'}
      title={artist.name}
      art={artist.image}
      actions={
        <a className="dsc-pulse-btn" href={artist.href}>
          View artist
        </a>
      }
      glowRgb={promo.glowRgb}
      rootRef={promo.ref}
      videoId={promo.videoId}
      playing={promo.playing}
      onUnplayable={promo.onUnplayable}
      onHoverChange={promo.setHover}
    />
  );
}

export function VideoRail({ title, subtitle, artists, videosOn }: VideoRailProps) {
  if (artists.length === 0) return null;
  return (
    <section className="dsc-video-rail" aria-label={title}>
      <header className="dsc-section-head">
        <h3>{title}</h3>
        <p>{subtitle}</p>
      </header>
      <div className="dsc-video-rail-track">
        {artists.map((a) => (
          <RailCard key={a.key} artist={a} videosOn={videosOn} />
        ))}
      </div>
    </section>
  );
}
