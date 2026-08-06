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

import { beatportSliderClasses } from '../-beatport.core';
import { useBeatportSection } from '../-beatport.use-section';
import { BeatportSlider } from './beatport-slider';

export interface BeatportSectionProps<T> {
  sectionKey: string;
  config: BeatportSliderConfig;
  load: (signal: AbortSignal) => Promise<T[] | null>;
  renderItem: (item: T, index: number) => ReactNode;
  renderPlaceholder?: (index: number) => ReactNode;
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

  if (status === 'failed') {
    // 'keep-static-markup' (hero) and 'nothing' (charts, DJ) both render
    // NOTHING here. They differ in intent — the hero has placeholder slides in
    // the page markup to fall back to and the other two simply show an empty
    // section — but neither draws anything of its own.
    if (config.onFailure !== 'error-block') return null;
    return (
      <div className={classes.loading}>
        <div className={classes.loadingContent}>
          <h3>❌ {errorTitle}</h3>
          <p>{errorMessage}</p>
        </div>
      </div>
    );
  }

  // Nothing to show, nothing drawn. The vanilla's spinner markup lives in the
  // page shell and is overwritten when the data lands, so a loading section
  // renders none of its own.
  //
  // DECLARED EQUIVALENT: `status !== 'ready'` behaves identically in every
  // state reachable today, and so does dropping this line entirely, since
  // BeatportSlider already returns null for an empty list. Mutation testing
  // says all three are the same, and they are.
  //
  // An earlier version of this comment claimed the content check was needed to
  // keep items on screen during reload(). That was wrong: BeatportSection does
  // not expose reload, so the path cannot be reached through this component.
  // The hook's item retention across a reload is real and tested there; the
  // justification for testing it HERE was not.
  if (items.length === 0) return null;

  return (
    <BeatportSlider
      config={config}
      items={items}
      renderItem={renderItem}
      renderPlaceholder={renderPlaceholder}
      trackId={trackId}
      prevButtonId={prevButtonId}
      nextButtonId={nextButtonId}
      indicatorsId={indicatorsId}
    />
  );
}
