import { useEffect, useState, type CSSProperties } from 'react';

import { appURL } from '@/platform/url-base';

import styles from './boot-loader.module.css';

let bootedThisSession = false;

/**
 * Full-screen SoulSync boot splash (breathing emblem + 115° masked metallic sheen).
 * Shows once per page load while `/player` hydrates, then fades out cleanly.
 */
export function PlayerBootOverlay({ ready }: { ready: boolean }) {
  const [visible, setVisible] = useState(() => !bootedThisSession);
  const [minElapsed, setMinElapsed] = useState(() => bootedThisSession);
  const [fading, setFading] = useState(false);

  useEffect(() => {
    if (!visible) return;
    const tMin = window.setTimeout(() => setMinElapsed(true), 950);
    const tMax = window.setTimeout(() => {
      bootedThisSession = true;
      setFading(true);
      window.setTimeout(() => setVisible(false), 400);
    }, 2400);
    return () => {
      window.clearTimeout(tMin);
      window.clearTimeout(tMax);
    };
  }, [visible]);

  useEffect(() => {
    if (!visible || !ready || !minElapsed) return;
    bootedThisSession = true;
    setFading(true);
    const t = window.setTimeout(() => setVisible(false), 400);
    return () => window.clearTimeout(t);
  }, [visible, ready, minElapsed]);

  if (!visible) return null;

  const logoUrl = appURL('/static/trans2.png');
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
      <div className={styles.loader}>
        <img src={logoUrl} alt="SoulSync" />
        <div className={styles.sheen} style={maskStyle} aria-hidden="true" />
      </div>
    </div>
  );
}
