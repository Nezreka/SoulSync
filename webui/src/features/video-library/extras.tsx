/**
 * A title's extras: its trailers, featurettes, deleted scenes. Read off the
 * title's own folder by the server (core/video/extras.py) and listed on the
 * library tab. Play opens one in place: the stream is asked for by the key
 * the listing gave, never a path.
 */

import { useState } from 'react';

import type { Extra } from './types';

import { formatBytes } from './format';
import s from './video-library.module.css';

export function extraUrl(kind: 'movie' | 'show', id: number, key: string) {
  return `/api/video/detail/${kind}/${id}/extras/${encodeURIComponent(key)}`;
}

export function Extras({
  kind,
  id,
  extras,
}: {
  kind: 'movie' | 'show';
  id: number;
  extras: Extra[] | undefined;
}) {
  const [playing, setPlaying] = useState<string | null>(null);
  if (!extras?.length) return null;
  return (
    <section data-extras="">
      <div className={s.sectionHead}>
        <h3 className={s.sectionTitle}>
          Extras <span className={s.count}>{extras.length}</span>
        </h3>
      </div>
      <ul className={s.folders}>
        {extras.map((e) => {
          const open = playing === e.key;
          return (
            <li key={e.key} className={s.folder} data-extra={e.key} style={{ flexWrap: 'wrap' }}>
              <div className={s.folderBody}>
                <div className={s.folderName}>{e.name}</div>
                <div className={s.path} title={e.filename}>
                  {e.kind}
                </div>
              </div>
              <span className={s.folderCount}>{e.size_bytes ? formatBytes(e.size_bytes) : ''}</span>
              <button
                type="button"
                className={open ? s.btn : s.quietBtn}
                aria-expanded={open}
                onClick={() => setPlaying(open ? null : e.key)}
              >
                {open ? 'Close' : 'Play'}
              </button>
              {open ? (
                <video
                  controls
                  autoPlay
                  preload="metadata"
                  aria-label={`${e.kind}: ${e.name}`}
                  src={extraUrl(kind, id, e.key)}
                  style={{ flexBasis: '100%', width: '100%', maxHeight: '70vh' }}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
