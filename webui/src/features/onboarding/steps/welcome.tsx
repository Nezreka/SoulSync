/**
 * Welcome. While it's up, discovery runs (kicked off by the root), so the
 * panel at the bottom fills in with what SoulSync found on its own. Restoring
 * a backup skips setup entirely.
 */

import { useRef, useState } from 'react';

import { restoreBackup } from '../api';
import { realEffects } from '../apply';
import { forgetSavedDraft, useOnboarding } from '../store';
import { ArrowIcon, Chip, LOGO_SRC, s, Spinner } from '../ui';

const SERVICE_NAMES: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  navidrome: 'Navidrome',
  slskd: 'slskd',
  prowlarr: 'Prowlarr',
  qbittorrent: 'qBittorrent',
  sabnzbd: 'SABnzbd',
};

function host(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

export function WelcomeStep({ onFinished }: { onFinished: () => void }) {
  const env = useOnboarding((st) => st.env);
  const found = useOnboarding((st) => st.found);
  const discovering = useOnboarding((st) => st.discovering);
  const preview = useOnboarding((st) => st.preview);
  const go = useOnboarding((st) => st.go);
  const file = useRef<HTMLInputElement>(null);
  const [restoring, setRestoring] = useState<'idle' | 'busy' | string>('idle');

  const server = (['plex', 'jellyfin', 'navidrome'] as const).find((k) => found[k]);
  const others = Object.keys(found).filter((k) => k !== server);

  async function restore(f: File) {
    if (preview) {
      setRestoring(
        'The preview never restores. In a real setup this loads the backup and skips the rest.',
      );
      return;
    }
    setRestoring('busy');
    try {
      const bundle = JSON.parse(await f.text());
      const result = await restoreBackup(bundle);
      if (!result.success) throw new Error(result.error || "That backup couldn't be restored");
      await realEffects.complete([]);
      forgetSavedDraft();
      onFinished();
      window.location.reload();
    } catch (error) {
      setRestoring(error instanceof Error ? error.message : "That backup couldn't be restored");
    }
  }

  return (
    <div className={s.center}>
      <div className={s.glow} aria-hidden="true" />
      <div className={s.hero}>
        <img className={s.heroLogo} src={LOGO_SRC} alt="SoulSync" />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <h1 className={s.heroTitle}>Welcome to SoulSync</h1>
          <p className={s.heroLead}>
            One home for your music, movies, TV and YouTube. Setup takes about five minutes, and you
            can change anything later.
          </p>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
          <button
            type="button"
            className={`${s.primary} ${s.big}`}
            onClick={() => go('account')}
            autoFocus
          >
            Set up SoulSync
            <ArrowIcon />
          </button>
          <button
            type="button"
            className={s.quiet}
            onClick={() => file.current?.click()}
            disabled={restoring === 'busy'}
          >
            {restoring === 'busy' ? <Spinner label="Restoring" /> : null}
            Restore from a SoulSync backup
          </button>
          <input
            ref={file}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f) void restore(f);
            }}
          />
          {restoring !== 'idle' && restoring !== 'busy' ? (
            <p className={s.error} role="alert">
              {restoring}
            </p>
          ) : null}
        </div>
      </div>

      <section className={s.looked} aria-live="polite">
        <div className={s.lookedHead}>
          {discovering ? (
            <>
              <Spinner label="Looking" />
              Looking around your network
            </>
          ) : (
            <Chip tone="ok">While you read this, SoulSync looked around</Chip>
          )}
        </div>
        <div className={s.lookedGrid}>
          <div>
            <span>Running in</span>
            <strong>{env ? (env.docker ? 'Docker' : 'Its own folder') : '…'}</strong>
          </div>
          <div>
            <span>Media server</span>
            <strong>
              {server
                ? `${SERVICE_NAMES[server]} at ${host(found[server])}`
                : discovering
                  ? '…'
                  : 'None found'}
            </strong>
          </div>
          <div>
            <span>Also found</span>
            <strong>
              {others.length
                ? others.map((k) => SERVICE_NAMES[k] ?? k).join(', ')
                : discovering
                  ? '…'
                  : 'Nothing else'}
            </strong>
          </div>
        </div>
      </section>
    </div>
  );
}
