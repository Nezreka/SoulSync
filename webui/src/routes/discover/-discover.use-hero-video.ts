import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';

import { useBackdropVideoId } from './-discover.backdrop';
import { useVideoSlot, videoStage } from './-discover.video-stage';

/**
 * the hero's hidden music video.
 *
 * the promo banners play on their own once they hold the stage; the hero
 * never does. behind the active slide sits the artist's top official video
 * (the artist-only backdrop lookup, the same one the artist banner uses),
 * and it only comes forward when the reader asks: preview on, the stage
 * grants this slot the sound, the player mounts, and only once youtube says
 * PLAYING does the page learn the video is live, so the still can fade out
 * into a moving picture instead of a black gap.
 *
 * the slot registers with the stage ONLY while previewing, so an idle hero
 * costs the stage nothing and nothing ever autoplays. the preview belongs
 * to one artist on one visit: it ends when the slide changes, when the hero
 * scrolls out of view, when video backdrops are switched off, when the
 * stage itself switches off (reduced motion, a hidden tab), or when youtube
 * refuses the video. soulsync's own player taking the sound back (its
 * `play` strips the stage's sound) does NOT end it: the video stays,
 * muted, until the reader closes it.
 *
 * closing it by hand is the one graceful exit: the sound goes back at
 * once, but the picture takes the css crossfade's 1.4s — the still fades
 * back in over the video as the video fades out — and only then does the
 * preview actually end. every other exit is instant. a reader who changes
 * their mind mid-fade reopens it where it stands.
 *
 * the preview holds its seat for its whole life with the stage's hover
 * priority, not on its sound claim: the sound can leave mid-preview
 * (soulsync's own player takes it back the moment a track starts, and
 * the close hands it back for the fade), and soundless, on a one-video
 * device, a fully visible banner below would outrank a half-scrolled
 * hero — its player would unmount and the preview would strand over a
 * still, or hard-cut instead of fading out.
 *
 * the video can also vanish from under a running preview: other discover
 * ui (the block list, the feedback menu) invalidates the whole
 * ['discover'] prefix, and a refetch that answers with no video leaves
 * the stage holding a seat for nothing, with no button left to close it.
 * a preview whose video is gone stops at once, like every other loss.
 */

/** the hero's one slot on the video stage (the promos are promo-*). */
export const HERO_VIDEO_SLOT_ID = 'hero-video';

/** the exit crossfade's length: the css fade on the still and the video */
const CLOSE_FADE_MS = 1400;

export interface HeroVideo {
  /** attach to the hero's root element, for the stage's visibility watch */
  ref: (el: HTMLElement | null) => void;
  /**
   * the active artist's video, or null. always null with backdrops off,
   * and null while the stage itself is off (reduced motion, a hidden
   * tab): the button keys off this, and a button whose preview could
   * never play is a dead control. the id returns with the stage.
   */
  videoId: string | null;
  /** mount the player: the preview is on AND the stage granted this slot */
  playing: boolean;
  /** the reader asked for the preview */
  preview: boolean;
  /** the reader is closing it: the exit crossfade is running */
  closing: boolean;
  /** youtube says the video is actually playing: the still may fade out */
  videoLive: boolean;
  /** this video is the one with sound */
  soundOn: boolean;
  /** start the preview, or close it */
  togglePreview: () => void;
  /** youtube refused this video: back to the still, for good */
  onUnplayable: (videoId: string) => void;
  /** the player's own live signal (BackdropVideo's onLiveChange) */
  onLiveChange: (live: boolean) => void;
}

/** the promo banner's rule: a video with sound pauses soulsync's player. */
function pauseSoulSyncPlayer() {
  const audio = document.getElementById('audio-player') as HTMLAudioElement | null;
  if (audio && !audio.paused) audio.pause();
}

export function useHeroVideo(artist: string | null | undefined, videosOn: boolean): HeroVideo {
  const [refused, setRefused] = useState<ReadonlySet<string>>(() => new Set());
  const [preview, setPreview] = useState(false);
  const [closing, setClosing] = useState(false);
  const [videoLive, setVideoLive] = useState(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearCloseTimer = useCallback(() => {
    if (closeTimer.current !== null) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);
  // registered as having a video exactly while previewing: the preview can
  // only start once this artist's video is in hand, and every path that
  // loses the video closes the preview with it. an idle hero never holds
  // the stage, and the stage can never start it on its own.
  const slot = useVideoSlot(HERO_VIDEO_SLOT_ID, preview);
  const setSlotSound = slot.setSound;
  const setSlotHover = slot.setHover;
  // the stage's own on/off, read live: the preview must not outlive the
  // stage switching off under it (reduced motion, a hidden tab, the pref).
  // after the slot call, so the stage is wired before anyone subscribes.
  const stageEnabled = useSyncExternalStore(videoStage.subscribe, () => videoStage.enabled);
  const fetched = useBackdropVideoId(artist, null, slot.seen && videosOn);
  const usable = fetched && !refused.has(fetched) ? fetched : null;
  // derived, not mirrored: a slide change must never expose the previous
  // artist's video, not even for the one commit a state mirror would lag.
  const videoId = videosOn ? usable : null;

  // the instant stop: every system exit lands here, mid-fade or not. it
  // also hands back the seat pin, so the pin never outlives the preview
  // it was holding. (an unmount needs no hand-back: the slot leaves the
  // stage entirely, pin and all.)
  const stopPreview = useCallback(() => {
    clearCloseTimer();
    setSlotSound(false);
    setSlotHover(false);
    setPreview(false);
    setClosing(false);
    setVideoLive(false);
  }, [clearCloseTimer, setSlotHover, setSlotSound]);

  // a pending fade never outlives the hero itself
  useEffect(() => () => clearCloseTimer(), [clearCloseTimer]);

  // a new slide is a new artist's video: the old preview ends with its slide
  useEffect(() => {
    stopPreview();
  }, [artist, stopPreview]);

  // scrolled away: the same teardown the banners get, preview and all
  useEffect(() => {
    if (preview && !slot.visible) stopPreview();
  }, [preview, slot.visible, stopPreview]);

  // backdrops switched off mid-preview: close it, don't leave it stuck
  useEffect(() => {
    if (!videosOn) stopPreview();
  }, [videosOn, stopPreview]);

  // the stage switched off mid-preview (reduced motion arrived, the tab
  // hid): the banners stop, and so does the preview — a lit close button
  // over a player the stage will never grant is a stuck control.
  useEffect(() => {
    if (preview && !stageEnabled) stopPreview();
  }, [preview, stageEnabled, stopPreview]);

  // the video vanished from under a running preview: an invalidation
  // elsewhere on the page refetched the backdrop lookup and it answered
  // with no video. this can't fire spuriously at the start — the preview
  // only opens on a video in hand, so a null id here is always a loss,
  // and every legitimate loss (a new slide, backdrops off, a refusal)
  // stops the preview through its own path anyway.
  useEffect(() => {
    if (preview && !videoId) stopPreview();
  }, [preview, videoId, stopPreview]);

  // the sound is claimed from an effect, not the click: the slot only
  // registers its video in the stage in an earlier effect on the same
  // commit, and the stage revokes a sound claim from a slot that has no
  // video yet. claiming after that registration makes the claim stick.
  // (it also fires only when the preview OPENS: soulsync's own player
  // taking the sound back mid-preview must not be fought.)
  useEffect(() => {
    if (preview) setSlotSound(true);
  }, [preview, setSlotSound]);

  const startPreview = useCallback(() => {
    // a switched-off stage grants nothing: don't open a preview that can
    // never play and would only be torn down by the effect above.
    if (!videoId || !stageEnabled) return;
    pauseSoulSyncPlayer();
    // the seat pin goes on with the preview and stays for its whole
    // life: the sound claim looks like it holds the seat, but it can be
    // taken back at any moment, and the pin is what keeps the promise
    // that the video stays, muted, until the reader closes it.
    setSlotHover(true);
    setPreview(true);
  }, [videoId, setSlotHover, stageEnabled]);

  // the graceful exit, for the reader's hand only: the sound is released
  // now, the preview itself ends when the crossfade has run its course.
  // the seat pin, held since the preview opened, is what carries the
  // player through the fade now that the sound is gone: soundless and
  // only partly on screen, the slot would lose a one-video device's seat
  // to any fuller banner and unmount mid-fade. the finalize below hands
  // the pin back.
  const closePreview = useCallback(() => {
    setSlotSound(false);
    setSlotHover(true);
    setClosing(true);
    clearCloseTimer();
    closeTimer.current = setTimeout(() => {
      closeTimer.current = null;
      setSlotHover(false);
      setPreview(false);
      setClosing(false);
      setVideoLive(false);
    }, CLOSE_FADE_MS);
  }, [clearCloseTimer, setSlotHover, setSlotSound]);

  const togglePreview = useCallback(() => {
    if (closing) {
      // changed their mind mid-fade: the slot never unregistered (the
      // preview stayed on throughout the fade), so the sound is reclaimed
      // right here rather than through the open effect, which only fires
      // when the preview opens. the seat pin stays on: it is held for the
      // preview's whole life, and this preview is still going.
      clearCloseTimer();
      setClosing(false);
      setSlotSound(true);
      pauseSoulSyncPlayer();
    } else if (preview) {
      closePreview();
    } else {
      startPreview();
    }
  }, [closing, preview, clearCloseTimer, closePreview, setSlotSound, startPreview]);

  // refused: this artist falls back to the still, and the preview closes
  const onUnplayable = useCallback(
    (id: string) => {
      setRefused((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
      stopPreview();
    },
    [stopPreview],
  );

  const onLiveChange = useCallback((live: boolean) => setVideoLive(live), []);

  return {
    ref: slot.ref,
    // the id is handed out only while the stage could actually grant the
    // preview: with the stage off there is no button and no player, and
    // both return when the stage does (the tab visible again, the motion
    // preference lifted). the guard above keys off the derived id, not
    // this one — a stage switching off mid-preview is its own stop path.
    videoId: stageEnabled ? videoId : null,
    playing: preview && slot.playing,
    preview,
    closing,
    videoLive,
    soundOn: slot.soundOn,
    togglePreview,
    onUnplayable,
    onLiveChange,
  };
}
