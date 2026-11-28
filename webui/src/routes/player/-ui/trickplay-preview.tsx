/**
 * Video Trickplay hover preview tile (Media Analysis Phase 4).
 * Slices the exact tile out of a Jellyfin-compatible <stem>.trickplay sprite sheet.
 */

import { appURL } from '@/platform/url-base';

export interface TrickplayManifest {
  tile_width: number;
  tile_height: number;
  cols: number;
  rows: number;
  interval_s: number;
  sprite_count: number;
  sprite_url_template: string;
}

export function TrickplayPreview({
  manifest,
  seconds,
  label,
}: {
  manifest: TrickplayManifest;
  seconds: number;
  label?: string;
}) {
  const interval = Math.max(1, manifest.interval_s || 10);
  const cols = Math.max(1, manifest.cols || 10);
  const rows = Math.max(1, manifest.rows || 10);
  const perSprite = cols * rows;
  const maxIdx = Math.max(0, manifest.sprite_count * perSprite - 1);
  const idx = Math.min(maxIdx, Math.max(0, Math.floor(Math.max(0, seconds) / interval)));
  const sprite = Math.floor(idx / perSprite);
  const within = idx % perSprite;
  const col = within % cols;
  const row = Math.floor(within / cols);
  const tw = manifest.tile_width || 320;
  const th = manifest.tile_height || 180;
  const scale = 200 / tw;
  const displayW = Math.round(tw * scale);
  const displayH = Math.round(th * scale);
  const url = appURL(manifest.sprite_url_template.replace('{index}', String(sprite)));

  return (
    <div
      style={{
        display: 'inline-flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 4,
        padding: 4,
        borderRadius: 8,
        background: 'rgba(10, 12, 18, 0.92)',
        border: '1px solid rgba(255,255,255,0.14)',
        boxShadow: '0 8px 24px rgba(0,0,0,0.45)',
      }}
    >
      <div
        style={{
          width: displayW,
          height: displayH,
          borderRadius: 4,
          overflow: 'hidden',
          backgroundImage: `url(${url})`,
          backgroundRepeat: 'no-repeat',
          backgroundSize: `${Math.round(cols * tw * scale)}px ${Math.round(rows * th * scale)}px`,
          backgroundPosition: `${-Math.round(col * tw * scale)}px ${-Math.round(row * th * scale)}px`,
        }}
      />
      {label ? (
        <span style={{ fontSize: 11, fontVariantNumeric: 'tabular-nums', opacity: 0.9 }}>
          {label}
        </span>
      ) : null}
    </div>
  );
}
