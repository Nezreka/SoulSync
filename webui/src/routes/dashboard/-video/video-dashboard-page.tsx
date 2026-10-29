/**
 * The video dashboard (/dashboard?media=video): the music dashboard's
 * structure on video's data. A hero (greeting, library size and the scan
 * strip, the worker orbs on their stage), then two columns: what you watch and what's new on the left, the system on the right.
 *
 * It is #dashboard-page on purpose, so every music dashboard rule (hero,
 * rails, rail cards, tiles, the phone layout) styles it the same. The music
 * orbs skip a stage marked data-foreign-orbs, and the video orbs anchor
 * [data-video-orb-stage] instead.
 *
 * No health band: system health is a state, and it lives in the notification
 * panel header, not in a dashboard block.
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';

import { QuickSettings, AutomationsCard } from '../-ui/automations-card';
import { calendarQueryOptions } from '../../calendar/-calendar.api';
import { addDays, isoOf } from '../../calendar/-calendar.model';
import { CalendarHero } from '../../calendar/-ui/calendar-hero';
import { findingsQuery, issuesQuery, scanQuery, statsQuery } from './-vdash.api';
import { videoHeroNumbers, WEEK_AHEAD_DAYS, WEEK_BACK_DAYS } from './-vdash.model';
import { ContinueBand, DownloadsBand, goVideo, RecentBand, StudiosBand } from './video-bands';
import styles from './video-dashboard.module.css';
import { VideoHero } from './video-hero';
import { VideoLibraryStrip } from './video-library-strip';
import { CountTiles, WeekCard } from './video-rail';

export function VideoDashboardPage({
  profileId,
  isAdmin,
  canDownload,
}: {
  profileId: number;
  isAdmin: boolean;
  canDownload: boolean;
}) {
  const stats = useQuery(statsQuery());
  const scan = useQuery(scanQuery());
  const issues = useQuery(issuesQuery());
  const findings = useQuery(findingsQuery(isAdmin));

  // one calendar read feeds the billboard (what's next) and the week card
  // (what aired): a week back and a week ahead of today
  const today = isoOf(new Date());
  const start = addDays(today, -WEEK_BACK_DAYS);
  const week = useQuery(
    calendarQueryOptions(profileId, {
      start,
      days: WEEK_BACK_DAYS + WEEK_AHEAD_DAYS + 1,
      scope: 'watchlist',
      specials: false,
    }),
  );

  // the orbs and the worker buttons are vanilla: point them at the stage
  // this page just mounted
  useEffect(() => {
    window.videoWorkerOrbs?.refresh();
    window.videoEnrichmentPrime?.();
  }, []);

  const autoOn = week.data?.automations?.airing?.enabled ?? true;
  const recent = stats.data?.recent;

  return (
    <div className="page-shell dashboard-container" id="dashboard-page" data-media="video">
      <VideoHero
        numbers={videoHeroNumbers(stats.data)}
        onNumber={() => goVideo('video-library')}
        library={<VideoLibraryStrip stats={stats.data} scan={scan.data} isAdmin={isAdmin} />}
      />
      <div className="dash-layout">
        <div className="dash-main">
          {week.data?.episodes.length ? (
            <div className={styles.next}>
              <CalendarHero
                episodes={week.data.episodes.filter((e) => e.air_date >= today)}
                today={today}
                now={Date.now()}
                currentWeek
                autoOn={autoOn}
                onOpen={(ep) => window.videoOpenCalendar?.({ date: ep.air_date, ep: ep.id })}
              />
            </div>
          ) : null}
          <ContinueBand />
          <RecentBand items={recent} />
          {isAdmin ? <StudiosBand recent={recent} /> : null}
        </div>
        <aside className="dash-side" aria-label="Your system">
          <WeekCard data={week.data} today={today} start={start} canDownload={canDownload} />
          <DownloadsBand />
          <AutomationsCard compact side="video" />
          <CountTiles
            watchlist={stats.data?.watchlist ?? null}
            wishlist={stats.data?.wishlist ?? null}
            issues={issues.data ?? 0}
            findings={findings.data ?? 0}
          />
        </aside>
      </div>
      <footer className="dash-footer">
        <QuickSettings />
      </footer>
    </div>
  );
}
