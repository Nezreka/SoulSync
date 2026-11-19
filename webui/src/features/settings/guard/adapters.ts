/**
 * The composite widgets: the ones a single control cannot stand for (a chain
 * you reorder, a ranked list, a list of rows you add to). Each has an adapter
 * here, written against TODAY's markup, and a short script of what a person
 * does with it. What each step sends is pinned in manifest.json under
 * `composites`.
 *
 * When a widget's markup changes (the new reorder list), repoint its adapter:
 * keep the function names and what they mean, change how they find things.
 * The scripts and the manifest stay as they are, and the guard then says
 * whether the new widget still sends what the old one did.
 */

import { fireEvent } from '@testing-library/react';

import type { Side } from './harness';

import { change } from './driver';

export interface Adapter {
  side: Side;
  what: string;
  /** any element of the widget: the guard finds the slot it is drawn in by it */
  root: () => Element | null;
  /** get the widget on screen (pick its tab), before the script */
  open?: () => void;
  /** what the widget shows now, in order */
  readOrder: () => string[];
  /** what a person does, one save each; each returns what it did, in words */
  script: Array<() => string>;
}

// ── small tools ──

const one = <T extends HTMLElement = HTMLElement>(
  selector: string,
  within: ParentNode = document,
) => within.querySelector<T>(selector);
const all = (selector: string, within: ParentNode = document) => [
  ...within.querySelectorAll<HTMLElement>(selector),
];
const wordsOf = (el: Element) =>
  el.getAttribute('aria-label') ||
  el.getAttribute('title') ||
  (el.textContent ?? '').replace(/\s+/g, ' ').trim();

function press(el: Element | null | undefined, what: string) {
  if (!el) throw new Error(`settings guard adapter: nothing to press for "${what}"`);
  fireEvent.click(el);
}
function type(el: Element | null | undefined, value: string, what: string) {
  if (!el) throw new Error(`settings guard adapter: no field for "${what}"`);
  change(el as HTMLElement, value);
}
/** the first button in `within` whose words pass the test */
const button = (within: ParentNode | null, test: (words: string) => boolean) =>
  within ? all('button', within).find((el) => test(wordsOf(el))) : undefined;

// ── the download chain (Downloads > Source Settings): one widget, three chains ──

const chain = {
  root: () => one('#download-chain-widget'),
  /** the chain's tab: Music, Video or Audiobooks */
  pick: (label: string) =>
    press(
      all('#dlchain-tabs [role="tab"]').find((tab) => wordsOf(tab) === label),
      `the ${label} chain`,
    ),
  // repointed for the order list (ui/order-list.tsx, settings-bic phase 1):
  // rows are li[data-id], the pool's "Use" adds, and the moves and the
  // removal are entries of the row's menu
  readOrder: () =>
    all('#download-chain-widget ul[aria-label] > li[data-id]').map((row) => row.dataset.id ?? ''),
  readPool: () =>
    all('#download-chain-widget [data-pool] > li[data-id]').map((row) => row.dataset.id ?? ''),
  add: (id: string) => press(one(`#download-chain-widget [data-use="${id}"]`), `add ${id}`),
  remove: (id: string) => {
    press(one(`#download-chain-widget [data-more="${id}"]`), `the menu of ${id}`);
    press(
      button(one('#download-chain-widget [role="menu"]'), (words) =>
        words.startsWith('Stop using'),
      ),
      `remove ${id}`,
    );
  },
  move: (id: string, delta: -1 | 1) => {
    press(one(`#download-chain-widget [data-more="${id}"]`), `the menu of ${id}`);
    press(
      button(one('#download-chain-widget [role="menu"]'), (words) =>
        words.startsWith(delta < 0 ? 'Move up' : 'Move down'),
      ),
      `move ${id}`,
    );
  },
};

/** add the first one waiting, move the last up, take the first out */
const chainScript = (c: {
  readOrder: () => string[];
  readPool: () => string[];
  add: (id: string) => void;
  remove: (id: string) => void;
  move: (id: string, delta: -1 | 1) => void;
}): Array<() => string> => [
  () => {
    const id = c.readPool()[0];
    c.add(id);
    return `add ${id}`;
  },
  () => {
    const id = c.readOrder().at(-1) ?? '';
    c.move(id, -1);
    return `move ${id} up`;
  },
  () => {
    const id = c.readOrder()[0];
    c.remove(id);
    return `remove ${id}`;
  },
];

const chainAdapter = (side: Side, label: string, what: string): Adapter => ({
  side,
  what,
  root: chain.root,
  open: () => chain.pick(label),
  readOrder: chain.readOrder,
  script: chainScript(chain),
});

// ── the subtitle provider chain (Library > Organization > Video) ──

const subtitles = {
  root: () => one('#subchain-widget'),
  readOrder: () => all('#subchain-list [data-src]').map((step) => step.dataset.src ?? ''),
  readPool: () => all('#subchain-pool [data-src]').map((tile) => tile.dataset.src ?? ''),
  /** the steps that can be moved and removed */
  movable: () =>
    all('#subchain-list [data-src]')
      .filter((step) => one('[data-action="remove"]', step))
      .map((step) => step.dataset.src ?? ''),
  add: (id: string) => press(one(`#subchain-pool [data-src="${id}"]`), `add ${id}`),
  remove: (id: string) =>
    press(one(`#subchain-list [data-src="${id}"] [data-action="remove"]`), `remove ${id}`),
  move: (id: string, delta: -1 | 1) =>
    press(
      one(`#subchain-list [data-src="${id}"] [data-action="${delta < 0 ? 'up' : 'down'}"]`),
      `move ${id}`,
    ),
};

// ── the music quality profile's ranked targets (Quality) ──

const targets = {
  root: () => one('#ranked-targets-list'),
  rows: () => all('#ranked-targets-list [data-index]'),
  readOrder: () => targets.rows().map((row) => one('.rt-label', row)?.textContent ?? ''),
  move: (index: number, delta: -1 | 1) =>
    press(
      button(targets.rows()[index], (words) => words === (delta < 0 ? 'Move up' : 'Move down')),
      `move target ${index}`,
    ),
  remove: (index: number) =>
    press(
      button(targets.rows()[index], (words) => words === 'Remove'),
      `remove target ${index}`,
    ),
  add: (format: string) => {
    type(one('#rt-add-format'), format, 'the format to add');
    press(
      button(one('#quality-profile-section'), (words) => words === '+ Add target'),
      'add a target',
    );
  },
};

// ── the video quality ladder (Quality > Video Quality) ──

const ladder = {
  root: () => one('#vq-tier-rows'),
  readOrder: () =>
    all('#vq-tier-rows [data-vq-tier-toggle]').map((box) => box.dataset.vqTierToggle ?? ''),
  move: (key: string, delta: -1 | 1) =>
    press(one(`#vq-tier-rows [data-vq-tier-move="${key}"][data-dir="${delta}"]`), `move ${key}`),
};

// ── the album art sources (Library > Post-processing) ──

const art = {
  root: () => one('#art-source-list'),
  rows: () => all('#art-source-list [data-source-id]'),
  /** every source in the order shown; the switched-on ones are what is saved */
  readOrder: () =>
    art
      .rows()
      .map(
        (row) =>
          `${row.dataset.sourceId}${one<HTMLInputElement>('input', row)?.checked ? '' : ' (off)'}`,
      ),
  row: (id: string) => one(`#art-source-list [data-source-id="${id}"]`),
  move: (id: string, delta: -1 | 1) =>
    press(
      button(art.row(id), (words) => words === (delta < 0 ? 'Move up' : 'Move down')),
      `move ${id}`,
    ),
  toggle: (id: string) => press(one('input[type="checkbox"]', art.row(id) ?? undefined), id),
};

// ── the HiFi instances (Sources > HiFi) ──

const hifi = {
  root: () => one('#hifi-instances-list'),
  rows: () => all('#hifi-instances-list [draggable][data-url]'),
  readOrder: () => hifi.rows().map((row) => row.dataset.url ?? ''),
  row: (url: string) => hifi.rows().find((row) => row.dataset.url === url),
  // phase 2: toggle and remove are real buttons now, marked by data attributes
  toggle: (url: string) => press(one('[data-hifi-toggle]', hifi.row(url)), `switch ${url}`),
  remove: (url: string) => press(one('[data-hifi-remove]', hifi.row(url)), `remove ${url}`),
  add: (url: string) => {
    type(one('#hifi-new-instance'), url, 'the new instance');
    press(
      button(one('#hifi-new-instance')?.closest('[data-field]') ?? null, (words) => words === 'Add'),
      'add an instance',
    );
  },
};

// ── a list of folders (Library > Folders): rows of one field and a remove ──

const folderList = (listId: string) => {
  const list = () => one(`#${listId}`);
  const fields = () => all('input[type="text"]', list() ?? undefined);
  /** the list's own Add button: the nearest one going up from the list */
  const adder = () => {
    for (let anc = list()?.parentElement; anc; anc = anc.parentElement) {
      const hit = button(anc, (words) => words.startsWith('+ Add'));
      if (hit) return hit;
    }
    return undefined;
  };
  return {
    root: list,
    readOrder: () => fields().map((field) => (field as HTMLInputElement).value),
    add: (path: string) => {
      press(adder(), `add a row to ${listId}`);
      type(fields().at(-1), path, `the new row of ${listId}`);
    },
    remove: (index: number) =>
      press(
        button(fields()[index]?.parentElement ?? null, () => true),
        `remove row ${index} of ${listId}`,
      ),
  };
};
const folderScript = (list: ReturnType<typeof folderList>, path: string): Array<() => string> => [
  () => {
    list.add(path);
    return `add ${path}`;
  },
  () => {
    list.remove(0);
    return 'remove the first row';
  },
];
const musicPaths = folderList('music-paths-list');
const samplePaths = folderList('sample-paths-list');

// ── the genre whitelist (Downloads > Filtering): chips and a field that adds ──

const genres = {
  root: () => one('#genre-whitelist-chips'),
  readOrder: () =>
    all('#genre-whitelist-chips [data-genre]').map((chip) => chip.dataset.genre ?? ''),
  remove: (genre: string) =>
    press(one(`#genre-whitelist-chips [data-genre="${genre}"]`), `remove ${genre}`),
  add: (genre: string) => {
    const field = one('#genre-whitelist-search');
    type(field, genre, 'the genre to add');
    if (field) fireEvent.keyDown(field, { key: 'Enter' });
  },
};

// ── which media server is the active one (Connections > Server Connections) ──

const SERVERS = ['plex', 'jellyfin', 'navidrome', 'music_assistant', 'soulsync'];
const server = {
  root: () => one('#plex-toggle'),
  /** the active one */
  readOrder: () => SERVERS.filter((id) => one(`#${id}-toggle`)?.classList.contains('active')),
  pick: (id: string) => press(one(`#${id}-toggle`), `make ${id} the server`),
};

// ── a new music quality profile (Quality > the profile list) ──

const profiles = {
  root: () => one('#qp-profile-list'),
  readOrder: () =>
    all('#qp-profile-list .qp-profile-text').map((row) =>
      (row.textContent ?? '').replace(/\s+/g, ' ').trim(),
    ),
  create: (name: string) => {
    press(one('#qp-profile-new-btn'), 'new profile');
    const field = one('#qp-profile-new-input');
    if (!field) throw new Error('settings guard adapter: the new profile field did not open');
    (field as HTMLInputElement).value = name;
    fireEvent.keyDown(field, { key: 'Enter' });
  },
};

// ── the registry ──

export const ADAPTERS: Record<string, Adapter> = {
  musicChain: chainAdapter('music', 'Music', 'the music download chain: download_source.*'),
  videoChain: chainAdapter(
    'music',
    'Video',
    'the video download chain: POST /api/video/downloads/config at once',
  ),
  audiobookChain: chainAdapter(
    'music',
    'Audiobooks',
    'the audiobook download chain: audiobooks.download_source.*',
  ),
  subtitleChain: {
    side: 'video',
    what: 'the subtitle provider chain',
    root: subtitles.root,
    readOrder: subtitles.readOrder,
    // the last step (local transcription) is fixed: it has no buttons
    script: [
      () => {
        const id = subtitles.movable().at(-1) ?? '';
        subtitles.move(id, -1);
        return `move ${id} up`;
      },
      () => {
        const id = subtitles.readOrder()[0];
        subtitles.remove(id);
        return `remove ${id}`;
      },
      () => {
        const id = subtitles.readPool()[0];
        subtitles.add(id);
        return `add ${id}`;
      },
    ],
  },
  musicTargets: {
    side: 'music',
    what: "the music quality profile's ranked targets",
    root: targets.root,
    readOrder: targets.readOrder,
    script: [
      () => {
        targets.move(1, -1);
        return 'move the second target up';
      },
      () => {
        targets.remove(2);
        return 'remove the third target';
      },
      () => {
        targets.add('alac');
        return 'add ALAC';
      },
    ],
  },
  videoLadder: {
    side: 'video',
    what: 'the video quality ladder (the tiers in order)',
    root: ladder.root,
    readOrder: ladder.readOrder,
    script: [
      () => {
        const key = ladder.readOrder()[1];
        ladder.move(key, -1);
        return `move ${key} up`;
      },
      () => {
        const key = ladder.readOrder()[0];
        ladder.move(key, 1);
        return `move ${key} down`;
      },
    ],
  },
  artOrder: {
    side: 'music',
    what: 'the album art sources: metadata_enhancement.album_art_order',
    root: art.root,
    readOrder: art.readOrder,
    script: [
      () => {
        const id = art.rows()[1]?.dataset.sourceId ?? '';
        art.move(id, -1);
        return `move ${id} up`;
      },
      () => {
        // the first one that is off: it stays where it is when it goes on
        const row = art.rows().find((r) => !one<HTMLInputElement>('input', r)?.checked);
        const id = row?.dataset.sourceId ?? '';
        art.toggle(id);
        return `switch ${id} on`;
      },
    ],
  },
  hifiInstances: {
    side: 'music',
    what: 'the HiFi instances',
    root: hifi.root,
    readOrder: hifi.readOrder,
    script: [
      () => {
        const url = hifi.readOrder()[0];
        hifi.toggle(url);
        return `switch ${url}`;
      },
      () => {
        const url = hifi.readOrder().at(-1) ?? '';
        hifi.remove(url);
        return `remove ${url}`;
      },
      () => {
        hifi.add('https://guard.example');
        return 'add https://guard.example';
      },
    ],
  },
  musicPaths: {
    side: 'music',
    what: 'the extra music library folders: library.music_paths',
    root: musicPaths.root,
    readOrder: musicPaths.readOrder,
    script: folderScript(musicPaths, '/guard/music'),
  },
  samplePaths: {
    side: 'music',
    what: 'the Sample Studio folders',
    root: samplePaths.root,
    readOrder: samplePaths.readOrder,
    script: folderScript(samplePaths, '/guard/samples'),
  },
  genreWhitelist: {
    side: 'music',
    what: 'the genre whitelist: genre_whitelist.genres',
    root: genres.root,
    readOrder: genres.readOrder,
    script: [
      () => {
        const genre = genres.readOrder()[0];
        genres.remove(genre);
        return `remove ${genre}`;
      },
      () => {
        genres.add('guardwave');
        return 'add guardwave';
      },
    ],
  },
  activeServer: {
    side: 'music',
    what: 'the active media server: active_media_server',
    root: server.root,
    readOrder: server.readOrder,
    script: [
      () => {
        server.pick('jellyfin');
        return 'make jellyfin the server';
      },
    ],
  },
  musicProfiles: {
    side: 'music',
    what: 'the music quality profiles: a new one',
    root: profiles.root,
    readOrder: profiles.readOrder,
    script: [
      () => {
        profiles.create('Guard');
        return 'create a profile named Guard';
      },
    ],
  },
};
