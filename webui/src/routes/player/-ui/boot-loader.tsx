import { useEffect, useState, type CSSProperties } from 'react';

import { appURL } from '@/platform/url-base';

import styles from './boot-loader.module.css';

let bootedThisSession = false;

/**
 * Full-screen SoulSync cinema OLED boot splash:
 * dual-lobe chromatic aura (warm magenta/amber + cool cyan/violet),
 * soft deblur reveal, micro-float breathe, and 115° dual-band prism sheen
 * masked strictly to the infinity ribbon silhouette.
 */
export function PlayerBootOverlay({ ready }: { ready: boolean }) {
  const [visible, setVisible] = useState(() => !bootedThisSession);
  const [minElapsed, setMinElapsed] = useState(() => bootedThisSession);
  const [fading, setFading] = useState(false);

  useEffect(() => {
    if (!visible) return;
    const tMin = window.setTimeout(() => setMinElapsed(true), 1150);
    const tMax = window.setTimeout(() => {
      bootedThisSession = true;
      setFading(true);
      window.setTimeout(() => setVisible(false), 420);
    }, 2600);
    return () => {
      window.clearTimeout(tMin);
      window.clearTimeout(tMax);
    };
  }, [visible]);

  useEffect(() => {
    if (!visible || !ready || !minElapsed) return;
    bootedThisSession = true;
    setFading(true);
    const t = window.setTimeout(() => setVisible(false), 420);
    return () => window.clearTimeout(t);
  }, [visible, ready, minElapsed]);

  if (!visible) return null;

  const logoUrl = appURL('/static/soulsync-mark.png');
  const maskStyle: CSSProperties = {
    WebkitMaskImage: `url("${logoUrl}")`,
    maskImage: `url("${logoUrl}")`,
  };

  return (
    <div
      className={styles.overlay}
      data-fading={fading ? 'true' : undefined}
      role="status"
      aria-label="Loading SoulSync"
    >
      <div className={styles.auraWarm} aria-hidden="true" />
      <div className={styles.auraCool} aria-hidden="true" />
      <div className={styles.stage}>
        <div className={styles.loader}>
          <img src={logoUrl} alt="SoulSync" />
          <div className={styles.sheen} style={maskStyle} aria-hidden="true" />
        </div>
        <div className={styles.wordmark} aria-hidden="true">
          <span className={styles.wordText}>SoulSync</span>
          <span className={styles.hairline} />
        </div>
      </div>
    </div>
  );
}
