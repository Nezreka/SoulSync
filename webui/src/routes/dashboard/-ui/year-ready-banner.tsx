/**
 * YearReadyBanner: "Your <year> is ready", Dec 1 to Jan 15.
 *
 * The server decides the season, whether this profile has a year to show and
 * whether it dismissed this year's banner (core/stats/year_extras.py), so the
 * same answer holds on every device. Renders nothing otherwise.
 */

import { useEffect, useState } from 'react';

import { dismissYearBanner, fetchYearBanner, type YearBanner } from '../../stats/-year.api';

export const YEAR_STORY_HREF = '/stats?story=year';

export function YearReadyBanner() {
  const [banner, setBanner] = useState<YearBanner | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchYearBanner().then((data) => {
      if (!cancelled && data?.show && data.year) setBanner(data);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!banner?.show || !banner.year) return null;

  const open = () => {
    const router = window.SoulSyncWebRouter;
    if (router?.navigateToHref) void router.navigateToHref(YEAR_STORY_HREF);
    else window.location.assign(YEAR_STORY_HREF);
  };

  const dismiss = async () => {
    setBanner(null);
    if (!(await dismissYearBanner())) {
      window.showToast?.("Couldn't hide that. Try again.", 'error');
      setBanner(banner);
    }
  };

  return (
    <section className="year-ready" aria-label={`Your ${banner.year} is ready`}>
      <span className="year-ready-glow" aria-hidden="true" />
      <div className="year-ready-text">
        <span className="year-ready-kicker">Your Year in Listening</span>
        <strong className="year-ready-title">Your {banner.year} is ready</strong>
        <span className="year-ready-sub">
          Your top artists, songs and days, with your songs playing along.
        </span>
      </div>
      <div className="year-ready-actions">
        <button type="button" className="year-ready-open" onClick={open}>
          Open your year
        </button>
        <button
          type="button"
          className="year-ready-dismiss"
          aria-label="Hide this"
          title="Hide until next year"
          onClick={() => void dismiss()}
        >
          ×
        </button>
      </div>
    </section>
  );
}
