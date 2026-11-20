/**
 * The Library tab (settings-bic DESIGN.md 3.1, 3.3, mockup 03): Folders,
 * Naming, Tags and artwork, Import and files, and Subtitles (the video side's
 * own), one pill each. Each pill reads itself back in a hero sentence and has
 * a Music / Audiobooks / Podcasts / Video switch (only the kinds it has
 * settings for), opening on the side the page is on. The switch is in the
 * address (`kind`), one per pill.
 *
 * Every pill and every kind stays mounted while another is shown, as on the
 * other rebuilt tabs: an edit that has not saved yet survives a switch, and
 * the search and the key guard find every field. Nothing of one kind shows
 * under another.
 */

import { type ComponentType, useEffect, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../../host';
import type { SettingsStore } from '../../settings-store';

import { nav, useNav } from '../../nav';
import { KindBar, KindSay, Segmented } from '../../ui';
import s from '../../ui/settings.module.css';
import { ViewPanel } from '../downloads/downloads-page';
import {
  AudiobookFolders,
  AudiobookFoldersHero,
  AudiobookImport,
  AudiobookImportHero,
  AudiobookNaming,
  AudiobookNamingHero,
  AudiobookTags,
  AudiobookTagsHero,
} from '../library-audiobooks';
import { type Media, MEDIA_LABEL } from '../library-card';
import {
  MusicFolders,
  MusicFoldersHero,
  PodcastFolders,
  PodcastFoldersHero,
} from '../library-folders';
import {
  MusicNaming,
  MusicNamingHero,
  PodcastImport,
  PodcastImportHero,
  PodcastNaming,
  PodcastNamingHero,
} from '../library-organization';
import { MusicImport, MusicImportHero, MusicTags, MusicTagsHero } from '../library-processing';
import { VideoFolders, VideoFoldersHero } from '../library-video-folders';
import {
  SubtitlesHero,
  VideoImport,
  VideoImportHero,
  VideoNaming,
  VideoNamingHero,
  VideoSubtitles,
  VideoTags,
  VideoTagsHero,
} from '../library-video-organization';

const TAB = 'library';

type Part = ComponentType<{ store: SettingsStore }>;

/** what each pill shows for each kind: its hero and its cards */
const PAGES: Record<string, Partial<Record<Media, { hero: Part; body: Part; say: string }>>> = {
  folders: {
    music: {
      hero: MusicFoldersHero,
      body: MusicFolders,
      say: 'Saves as you type. Each folder is checked as you go.',
    },
    audiobooks: {
      hero: AudiobookFoldersHero,
      body: AudiobookFolders,
      say: 'Saves as you type.',
    },
    podcasts: { hero: PodcastFoldersHero, body: PodcastFolders, say: 'Saves as you type.' },
    video: {
      hero: () => <VideoFoldersHero />,
      body: VideoFoldersBody,
      say: 'A folder saves when you leave it. More libraries save with their own Save.',
    },
  },
  naming: {
    music: { hero: MusicNamingHero, body: MusicNaming, say: 'Saves with the page.' },
    audiobooks: { hero: AudiobookNamingHero, body: AudiobookNaming, say: 'Saves with the page.' },
    podcasts: { hero: PodcastNamingHero, body: PodcastNaming, say: 'Saves with the page.' },
    video: {
      hero: () => <VideoNamingHero />,
      body: () => <VideoNaming />,
      say: 'A template saves when you leave it; a pick saves at once.',
    },
  },
  tags: {
    music: { hero: MusicTagsHero, body: MusicTags, say: 'Saves with the page.' },
    audiobooks: { hero: AudiobookTagsHero, body: AudiobookTags, say: 'Saves with the page.' },
    video: { hero: () => <VideoTagsHero />, body: () => <VideoTags />, say: 'Saves at once.' },
  },
  import: {
    music: { hero: MusicImportHero, body: MusicImport, say: 'Saves with the page.' },
    audiobooks: { hero: AudiobookImportHero, body: AudiobookImport, say: 'Saves with the page.' },
    podcasts: { hero: PodcastImportHero, body: PodcastImport, say: 'Saves with the page.' },
    video: {
      hero: () => <VideoImportHero />,
      body: () => <VideoImport />,
      say: 'A switch saves at once; a field when you leave it.',
    },
  },
};

function VideoFoldersBody({ store }: { store: SettingsStore }) {
  const settings = useSyncExternalStore(store.subscribe, () => store.getState().settings);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  // null until the settings load: both deployment guides show
  return <VideoFolders docker={loads ? !!settings._environment?.docker : null} />;
}

function useSide(): 'music' | 'video' {
  return useSyncExternalStore(
    (listener) => {
      const observer = new MutationObserver(listener);
      observer.observe(document.body, { attributes: true, attributeFilter: ['data-side'] });
      return () => observer.disconnect();
    },
    () => (document.body.getAttribute('data-side') === 'video' ? 'video' : 'music'),
    () => 'music',
  );
}

/** a pill's kind: the address when it names one this pill has, else the side */
export function kindFor(view: string, asked: string | null, side: 'music' | 'video'): Media {
  const kinds = Object.keys(PAGES[view] ?? {}) as Media[];
  if (asked && kinds.includes(asked as Media)) return asked as Media;
  return kinds.includes(side) ? side : (kinds[0] ?? 'music');
}

function KindPage({
  view,
  store,
  here,
  asked,
  side,
}: {
  view: string;
  store: SettingsStore;
  here: boolean;
  asked: string | null;
  side: 'music' | 'video';
}) {
  const pages = PAGES[view];
  const kinds = Object.keys(pages) as Media[];
  const kind = kindFor(view, here ? asked : null, side);
  const Hero = pages[kind]!.hero;
  return (
    <>
      <Hero store={store} />
      <KindBar>
        <Segmented
          id={`stg-library-${view}-kind`}
          label="Which kind of media these settings are for"
          value={kind}
          options={kinds.map((k) => ({ value: k, label: MEDIA_LABEL[k] }))}
          panel={(value) => `stg-library-${view}-${value}`}
          onChange={(next) => nav.patch({ kind: next })}
        />
        <KindSay>{pages[kind]!.say}</KindSay>
      </KindBar>
      {kinds.map((k) => {
        const Body = pages[k]!.body;
        return (
          <div
            key={k}
            id={`stg-library-${view}-${k}`}
            role="tabpanel"
            aria-label={MEDIA_LABEL[k]}
            data-kind={k}
            className={s.cards}
            hidden={k !== kind}
          >
            <Body store={store} />
          </div>
        );
      })}
    </>
  );
}

export function LibraryPage({ store }: SettingsTabProps) {
  const state = useNav();
  const side = useSide();
  const here = state.tab === TAB;
  const view = here ? state.view : 'folders';

  // the video settings load with the tab (soulsync:library-settings-shown,
  // settings-page.tsx); the subtitles pill shows the video side's
  useEffect(() => {
    if (here && view === 'subtitles')
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
  }, [here, view]);

  return (
    <div data-stg-new={TAB} hidden={!here}>
      {['folders', 'naming', 'tags', 'import'].map((key) => (
        <ViewPanel key={key} tab={TAB} view={key} open={view === key}>
          <KindPage
            view={key}
            store={store}
            here={here && view === key}
            asked={state.kind}
            side={side}
          />
        </ViewPanel>
      ))}
      <ViewPanel tab={TAB} view="subtitles" open={view === 'subtitles'}>
        <SubtitlesHero />
        <div className={s.cards}>
          <VideoSubtitles />
        </div>
      </ViewPanel>
    </div>
  );
}

/** the Library tab: one page with its pills (settings-bic phase 4) */
export function LibraryTab(props: SettingsTabProps) {
  if (props.part === 'page') return <LibraryPage {...props} />;
  return null;
}
