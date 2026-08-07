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

  /**
   * The section's placeholder, which in the vanilla is PAGE MARKUP rather than
   * anything a loader draws — and which the port therefore has to draw itself,
   * because the flip deletes that markup. See BeatportSliderConfig.
   */
  const placeholder = (
    <div className={classes.loading}>
      <div className={classes.loadingContent}>
        <h3>{config.loadingTitle}</h3>
        <p>{config.loadingSubtitle}</p>
      </div>
    </div>
  );

  if (status === 'failed') {
    if (config.onFailure === 'error-block') {
      return (
        <div className={classes.loading}>
          <div className={classes.loadingContent}>
            <h3>❌ {errorTitle}</h3>
            <p>{errorMessage}</p>
          </div>
        </div>
      );
    }
    // 'keep-static-markup' (hero) and 'nothing' (charts, DJ) replace NOTHING in
    // the vanilla, so what a user is left looking at is the placeholder block
    // the page shipped with — permanently. That is arguably a bug, but it is
    // what happens today, and drawing nothing instead would leave a blank strip
    // once the markup is gone.
    return placeholder;
  }

  // Same reasoning while loading: the section shows its own copy from the first
  // paint, because the markup that used to provide it is going away.
  //
  // DECLARED EQUIVALENT: `status !== 'ready'` behaves identically in every
  // state reachable today — the hook only reports 'ready' for a non-empty
  // result or for a cache entry that was non-empty when it was stored.
  if (items.length === 0) return placeholder;

  return (
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
    />
  );
}
