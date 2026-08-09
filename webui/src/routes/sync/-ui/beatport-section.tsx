/**
 * A Beatport slider section: load, then either the slider or the section's own
 * failure behaviour.
 *
 * The three failure arms are the whole reason this exists. The vanilla's five
 * sections disagree about what a failed load looks like — an error block, or
 * nothing at all, or leaving the static placeholder markup alone — and that
 * difference is invisible until Beatport is down.
 */

import type { ReactNode } from 'react';

import type { BeatportSliderConfig } from '../-beatport.core';
import type { BeatportSlideAttributes } from './beatport-slider';

import { beatportSliderClasses } from '../-beatport.core';
import { useBeatportSection } from '../-beatport.use-section';
import { BeatportSlider } from './beatport-slider';

export interface BeatportSectionProps<T> {
  sectionKey: string;
  config: BeatportSliderConfig;
  load: (signal: AbortSignal) => Promise<T[] | null>;
  renderItem: (item: T, index: number) => ReactNode;
  renderPlaceholder?: (index: number) => ReactNode;
  /**
   * Forwarded to the slider. Only the hero uses it, and only the hero needs
   * it — its artwork is painted by an attribute selector ON THE SLIDE. A
   * section that did not pass this through would render a hero with no
   * background and nothing would fail, which is why it is wired here rather
   * than left to each caller to remember.
   */
  slideAttributes?: (item: T, index: number) => BeatportSlideAttributes | undefined;
  /** e.g. 'Error Loading Releases'. Only used by the error-block sections. */
  errorTitle?: string;
  defaultErrorMessage?: string;
  trackId?: string;
  prevButtonId?: string;
  nextButtonId?: string;
  indicatorsId?: string;
}

export function BeatportSection<T>({
  sectionKey,
  config,
  load,
  renderItem,
  renderPlaceholder,
  slideAttributes,
  errorTitle = 'Error Loading Content',
  defaultErrorMessage = 'Failed to load',
  trackId,
  prevButtonId,
  nextButtonId,
  indicatorsId,
}: BeatportSectionProps<T>) {
  const { status, items, errorMessage } = useBeatportSection<T>({
    sectionKey,
    config,
    load,
    defaultErrorMessage,
  });
  const classes = beatportSliderClasses(config.slug);
  const Heading = config.loadingHeadingLevel;

  /**
   * THE SECTION FRAME. index.html wraps each grid slider in
   * `.beatport-{slug}-section > .beatport-{slug}-header > h2 + p`, then the
   * slider container (2936, 2973, 3011, 3048). The hero has no frame at all —
   * `#beatport-rebuild-content` opens straight onto the slider container
   * (2817-2819) — so `sectionHeading: null` renders the body bare rather than
   * emitting empty boxes with no stylesheet behind them.
   */
  const frame = (body: ReactNode) =>
    config.sectionHeading === null ? (
      body
    ) : (
      <div className={classes.section}>
        <div className={classes.header}>
          <h2 className={classes.title}>{config.sectionHeading.title}</h2>
          <p className={classes.subtitle}>{config.sectionHeading.subtitle}</p>
        </div>
        {body}
      </div>
    );

  /**
   * Both the placeholder and the error block sit INSIDE the slider's three
   * boxes, because that is literally where the vanilla puts them:
   * `sliderTrack.innerHTML = '<div class="beatport-{slug}-loading">…'`
   * (beatport-ui.js 644-647 and its twins), and the static placeholder is
   * nested in the track in the markup too. Rendered bare — as the port did —
   * they lose the container's width and the slider's height, so a loading or
   * failed section collapsed exactly the way the hero did.
   */
  const inSlider = (body: ReactNode) => (
    <div className={classes.container}>
      <div className={classes.slider}>
        <div className={classes.track} id={trackId}>
          {body}
        </div>
      </div>
    </div>
  );

  /**
   * The section's placeholder, which in the vanilla is PAGE MARKUP rather than
   * anything a loader draws — and which the port therefore has to draw itself,
   * because the flip deletes that markup. See BeatportSliderConfig.
   */
  const placeholder = inSlider(
    <div className={classes.loading}>
      <div className={classes.loadingContent}>
        <Heading>{config.loadingTitle}</Heading>
        <p>{config.loadingSubtitle}</p>
      </div>
    </div>,
  );

  if (status === 'failed') {
    if (config.onFailure === 'error-block') {
      return frame(
        inSlider(
          <div className={classes.loading}>
            <div className={classes.loadingContent}>
              <Heading>❌ {errorTitle}</Heading>
              <p>{errorMessage}</p>
            </div>
          </div>,
        ),
      );
    }
    // 'keep-static-markup' (hero) and 'nothing' (charts, DJ) replace NOTHING in
    // the vanilla, so what a user is left looking at is the placeholder block
    // the page shipped with — permanently. That is arguably a bug, but it is
    // what happens today, and drawing nothing instead would leave a blank strip
    // once the markup is gone.
    return frame(placeholder);
  }

  // Same reasoning while loading: the section shows its own copy from the first
  // paint, because the markup that used to provide it is going away.
  //
  // DECLARED EQUIVALENT: `status !== 'ready'` behaves identically in every
  // state reachable today — the hook only reports 'ready' for a non-empty
  // result or for a cache entry that was non-empty when it was stored.
  if (items.length === 0) return frame(placeholder);

  return frame(
    <BeatportSlider
      config={config}
      items={items}
      renderItem={renderItem}
      renderPlaceholder={renderPlaceholder}
      slideAttributes={slideAttributes}
      trackId={trackId}
      prevButtonId={prevButtonId}
      nextButtonId={nextButtonId}
      indicatorsId={indicatorsId}
    />,
  );
}
