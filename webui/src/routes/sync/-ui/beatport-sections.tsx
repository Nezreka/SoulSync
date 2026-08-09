/**
 * The five Beatport slider sections, wired end to end: load → cards → click →
 * download modal.
 *
 * Everything that differs between them already lives in data — BEATPORT_SLIDERS
 * for the layout and failure behaviour, the loaders for the payload field and
 * error copy, the card components for the markup. What is left here is the
 * wiring, and the two things the wiring alone decides:
 *
 *  - WHICH CLICK HANDLER a card gets. All four release-ish sections hand off to
 *    openBeatportRelease; the two chart sections hand off to
 *    openBeatportChartCard with their own variant.
 *  - WHETHER a card is clickable at all. Three of the four apply the vanilla's
 *    url test and attach nothing when it fails.
 *
 * The element ids are the vanilla's, from index.html 2821-3076. They are not
 * decoration: the stylesheet and the remaining vanilla both address them.
 */

import type { CSSProperties } from 'react';

import type { BeatportChart, BeatportHeroTrack, BeatportRelease } from '../-beatport.api';
import type { BeatportDownloadEnv } from '../-beatport.downloads';

import { BEATPORT_SLIDERS } from '../-beatport.core';
import { openBeatportChartCard, openBeatportRelease } from '../-beatport.downloads';
import {
  heroClickRelease,
  isBeatportReleaseClickable,
  loadBeatportDJCharts,
  loadBeatportFeaturedCharts,
  loadBeatportHero,
  loadBeatportHypePicks,
  loadBeatportNewReleases,
} from '../-beatport.loaders';
import {
  BeatportChartCard,
  BeatportHeroSlide,
  BeatportHypePickCard,
  BeatportHypePickPlaceholder,
  BeatportReleaseCard,
  BeatportReleasePlaceholder,
  heroSlideAttributes,
} from './beatport-cards';
import { BeatportSection } from './beatport-section';

export interface BeatportSectionEnvProps {
  env: BeatportDownloadEnv;
}

/* ── Hero (14-322) ────────────────────────────────────────────────────────── */

export function BeatportHeroSection({ env }: BeatportSectionEnvProps) {
  return (
    <BeatportSection<BeatportHeroTrack>
      sectionKey="beatport:hero"
      config={BEATPORT_SLIDERS.hero}
      load={loadBeatportHero}
      trackId="beatport-rebuild-slider-track"
      renderItem={(track, index) => <BeatportHeroSlide key={index} track={track} />}
      /**
       * On the SLIDE, not on a child. `.beatport-rebuild-slide[data-image]::before`
       * reads `var(--slide-bg-image)`, so both the attribute and the property
       * have to live here or the artwork silently never paints.
       *
       * The click rides along for the same reason — the vanilla binds it to the
       * slide element (141). Its `closest('.beatport-rebuild-nav-btn')` guard is
       * not transcribed: the nav buttons and indicators are SIBLINGS of the
       * track in this component, not descendants of a slide, so a click on them
       * cannot reach this handler. Checked in beatport-slider.tsx rather than
       * assumed.
       */
      slideAttributes={(track) => {
        const attributes = heroSlideAttributes(track);
        if (!isBeatportReleaseClickable(track.url)) return attributes;
        return {
          ...attributes,
          // 152: the vanilla sets this only on the clickable slides.
          style: { ...(attributes.style as CSSProperties), cursor: 'pointer' },
          onClick: () => {
            void openBeatportRelease(heroClickRelease(track), env);
          },
        };
      }}
    />
  );
}

/* ── New releases (339-661) ───────────────────────────────────────────────── */

export function BeatportNewReleasesSection({ env }: BeatportSectionEnvProps) {
  return (
    <BeatportSection<BeatportRelease>
      sectionKey="beatport:new-releases"
      config={BEATPORT_SLIDERS.releases}
      load={loadBeatportNewReleases}
      trackId="beatport-releases-slider-track"
      indicatorsId="beatport-releases-slider-indicators"
      errorTitle="Error Loading Releases"
      defaultErrorMessage="No releases available"
      renderItem={(release, index) => (
        <BeatportReleaseCard
          key={index}
          release={release}
          onClick={
            isBeatportReleaseClickable(release.url)
              ? () => {
                  void openBeatportRelease(release, env);
                }
              : undefined
          }
        />
      )}
      // 454-467: padded to ten with captioned filler cards, which the vanilla
      // then excludes from click handling by class. Here they simply have no
      // handler to exclude.
      renderPlaceholder={(index) => <BeatportReleasePlaceholder key={`filler-${index}`} />}
    />
  );
}

/* ── Hype picks (683-1005) ────────────────────────────────────────────────── */

export function BeatportHypePicksSection({ env }: BeatportSectionEnvProps) {
  return (
    <BeatportSection<BeatportRelease>
      sectionKey="beatport:hype-picks"
      config={BEATPORT_SLIDERS.hypePicks}
      load={loadBeatportHypePicks}
      trackId="beatport-hype-picks-slider-track"
      indicatorsId="beatport-hype-picks-slider-indicators"
      errorTitle="Error Loading Hype Picks"
      defaultErrorMessage="No hype picks available"
      renderItem={(release, index) => (
        <BeatportHypePickCard
          key={index}
          release={release}
          // The vanilla re-reads the rendered card text here instead of closing
          // over the release (961-972). Traced: that text reaches only the two
          // toasts, never the download — see the note on BeatportHypePickCard.
          onClick={
            isBeatportReleaseClickable(release.url)
              ? () => {
                  void openBeatportRelease(release, env);
                }
              : undefined
          }
        />
      )}
      // 780-783: an icon and nothing else, unlike the releases slider's filler.
      renderPlaceholder={(index) => <BeatportHypePickPlaceholder key={`filler-${index}`} />}
    />
  );
}

/* ── Featured charts (1018-1298) and DJ charts (1314-1603) ────────────────── */

export function BeatportFeaturedChartsSection({ env }: BeatportSectionEnvProps) {
  return (
    <BeatportSection<BeatportChart>
      sectionKey="beatport:featured-charts"
      config={BEATPORT_SLIDERS.charts}
      load={loadBeatportFeaturedCharts}
      trackId="beatport-charts-slider-track"
      indicatorsId="beatport-charts-slider-indicators"
      // No errorTitle: this section renders nothing on failure, so the prop
      // would never be read. Passing one would suggest otherwise.
      renderItem={(chart, index) => (
        <BeatportChartCard
          key={index}
          chart={chart}
          variant="chart"
          // 1158 attaches the handler unconditionally — the url test lives
          // inside handleBeatportChartCardClick, which toasts. Unlike the three
          // release sections, which refuse to bind at all.
          onClick={() => {
            void openBeatportChartCard(chart, 'chart', env);
          }}
        />
      )}
    />
  );
}

export function BeatportDJChartsSection({ env }: BeatportSectionEnvProps) {
  return (
    <BeatportSection<BeatportChart>
      sectionKey="beatport:dj-charts"
      config={BEATPORT_SLIDERS.dj}
      load={loadBeatportDJCharts}
      trackId="beatport-dj-slider-track"
      indicatorsId="beatport-dj-slider-indicators"
      renderItem={(chart, index) => (
        <BeatportChartCard
          key={index}
          chart={chart}
          variant="dj"
          onClick={() => {
            void openBeatportChartCard(chart, 'dj', env);
          }}
        />
      )}
    />
  );
}
