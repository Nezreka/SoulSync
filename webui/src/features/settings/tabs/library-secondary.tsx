/**
 * #2332: a secondary library. albums a rule matches (holiday music, a genre,
 * an album title) are filed in their own folder instead of the main library,
 * so the media server can show that folder as a library of its own and hide
 * it from listeners off season. off by default (core/library_rules.py).
 *
 * the rule decides per album, never per track, so an album is never split.
 * the albums already owned can be moved over (preview first). the folder is
 * also added to the additional music libraries, so SoulSync reads it.
 */

import { useEffect, useState } from 'react';

import type { SettingsStore } from '../settings-store';

import { Btn, Card, Row, SectionLabel, Switch, TextField } from '../ui';
import s from '../ui/settings.module.css';
import {
  LibPath,
  checkAttrs,
  shell,
  toast,
  useLoadedDraft,
  useLoads,
  useSettings,
} from './library-card';
import { text } from './source-panels';

const KEY = (name: string) => `secondary_${name}`;

type Check = {
  checked?: boolean;
  hidden?: boolean;
  server?: string;
  folder?: string;
  name?: string;
  message?: string;
};
type Item = {
  album_id: number;
  title: string;
  artist: string;
  why: string;
  action: string;
  src: string;
  dst: string;
};
type MoveResult = {
  success?: boolean;
  error?: string;
  items?: Item[];
  to_move?: number;
  moved?: number;
  failed?: number;
};

const WHY: Record<string, string> = {
  holiday: 'holiday music',
  genre: 'genre',
  'album title': 'album title',
};

/** what the card shows from the saved settings */
export function secondaryValues(library: Record<string, unknown>) {
  return {
    enabled: library[KEY('enabled')] === true,
    path: text(library[KEY('path')]),
    name: text(library[KEY('name')]) || 'Holiday',
    holiday: library[KEY('match_holiday')] !== false,
    genres: text(library[KEY('genres')]),
    titles: text(library[KEY('album_titles')]),
  };
}

export function SecondaryLibrary({
  store,
  onFolder,
}: {
  store: SettingsStore;
  /** the folder joins the additional music libraries list */
  onFolder?: (path: string) => void;
}) {
  const settings = useSettings(store);
  const loads = useLoads(store);
  const values = secondaryValues((settings.library || {}) as Record<string, unknown>);
  const set = (key: string, value: unknown) => store.set('library', KEY(key), value);
  const name = useLoadedDraft(store, values.name, (raw) => set('name', raw.trim() || 'Holiday'));
  const genres = useLoadedDraft(store, values.genres, (raw) => set('genres', raw));
  const titles = useLoadedDraft(store, values.titles, (raw) => set('album_titles', raw));
  const [check, setCheck] = useState<Check | null>(null);
  const [plan, setPlan] = useState<{ busy: boolean; items: Item[] | null; say: string }>({
    busy: false,
    items: null,
    say: '',
  });
  const noRule = !values.holiday && !values.genres.trim() && !values.titles.trim();

  useEffect(() => {
    if (!loads) return;
    void fetch('/api/secondary-library')
      .then((r) => r.json() as Promise<{ check?: Check }>)
      .then((data) => setCheck(data.check || null))
      .catch(() => setCheck(null));
  }, [loads]);

  useEffect(() => {
    if (loads && values.enabled && values.path.trim()) onFolder?.(values.path.trim());
  }, [loads, values.enabled, values.path]); // eslint-disable-line react-hooks/exhaustive-deps

  async function move(apply: boolean) {
    setPlan((p) => ({ ...p, busy: true }));
    try {
      const response = await fetch('/api/secondary-library/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apply }),
      });
      const data = (await response.json()) as MoveResult;
      if (!data.success) throw new Error(data.error || 'failed');
      const items = data.items || [];
      if (!apply) {
        const todo = data.to_move || 0;
        setPlan({
          busy: false,
          items,
          say: todo
            ? `${todo} album${todo === 1 ? '' : 's'} would move to ${values.name}.`
            : 'Nothing to move: no album in your main library matches.',
        });
        return;
      }
      const moved = data.moved || 0;
      setPlan({
        busy: false,
        items,
        say:
          `Moved ${moved} album${moved === 1 ? '' : 's'}.` +
          (data.failed ? ` ${data.failed} failed, see below.` : '') +
          ' Your media server was asked to rescan.',
      });
      toast(`Moved ${moved} album${moved === 1 ? '' : 's'} to ${values.name}`, 'success');
    } catch (error) {
      setPlan({
        busy: false,
        items: null,
        say: 'Could not move: ' + (error instanceof Error ? error.message : String(error)),
      });
    }
  }

  async function confirmMove() {
    const todo = (plan.items || []).filter((i) => i.action === 'move').length;
    const confirm = shell().showConfirmDialog;
    if (confirm) {
      const ok = await confirm({
        title: `Move ${todo} album${todo === 1 ? '' : 's'} to ${values.name}?`,
        message:
          'Each album folder moves to the same place inside the secondary folder. Nothing is overwritten, and your media server is asked to rescan.',
        confirmText: 'Move',
        cancelText: 'Not now',
      });
      if (!ok) return;
    }
    await move(true);
  }

  const toMove = (plan.items || []).filter((i) => i.action === 'move').length;

  return (
    <>
      <SectionLabel
        title="Secondary library"
        hint="Holiday music, kids' music or anything else in a folder of its own"
      />
      <Card id="secondary-library">
        {check?.hidden ? (
          <div className={s.rowSay} data-tone="bad" role="alert" id="secondary-library-hidden">
            {check.message ||
              `${(check.server || 'Your media server').replace(/^\w/, (c) => c.toUpperCase())} returned none of the music in ${check.folder}. Give SoulSync's account access to that library again.`}
          </div>
        ) : null}
        <Row
          label="File matching albums in their own folder"
          htmlFor="secondary-enabled"
          help="Albums a rule below matches go to the secondary folder instead of your music library folder."
          more={
            <>
              Add that folder to your media server as its own library. Listener accounts can lose
              access to it off season, so it stays out of their mixes.{' '}
              <strong>
                SoulSync&apos;s own media server account must keep access to it all year.
              </strong>{' '}
              Otherwise those albums look missing to the server. SoulSync still counts them as owned
              from its own files, and warns you after a scan when the server returns none of them.
              On Navidrome an admin account always sees every library.
            </>
          }
        >
          <Switch
            id="secondary-enabled"
            checked={values.enabled}
            onChange={(event) => set('enabled', event.target.checked)}
          />
        </Row>
        <Row
          wide
          sub
          off={!values.enabled}
          label="Secondary folder"
          htmlFor="secondary-path"
          help="Outside your music library folder, not inside it. Files keep your naming template."
        >
          <LibPath
            store={store}
            id="secondary-path"
            check={checkAttrs('music', 'Secondary folder')}
            placeholder="/music-holiday"
            value={values.path}
            onChange={(v) => set('path', v)}
          />
        </Row>
        <Row sub field off={!values.enabled} label="Name" htmlFor="secondary-name">
          <TextField
            id="secondary-name"
            placeholder="Holiday"
            value={name.value}
            onChange={(event) => name.onChange(event.target.value)}
          />
        </Row>
        <Row
          sub
          off={!values.enabled}
          label="Holiday music"
          htmlFor="secondary-match-holiday"
          help="Albums that are holiday music: a Christmas or holiday genre, a title like A Very Special Christmas, or your Holiday music flag."
          more="Only what is true of the whole album counts, so one carol on a pop record leaves the record where it is. Billie Holiday, Madonna's Holiday and other names never count."
        >
          <Switch
            id="secondary-match-holiday"
            checked={values.holiday}
            onChange={(event) => set('match_holiday', event.target.checked)}
          />
        </Row>
        <Row
          sub
          field
          off={!values.enabled}
          label="Genre contains"
          htmlFor="secondary-genres"
          help="Words split by | or commas, like children|kids. Checks the album's genres and the artist's main genre."
        >
          <TextField
            id="secondary-genres"
            wide
            placeholder="children|kids"
            value={genres.value}
            onChange={(event) => genres.onChange(event.target.value)}
          />
        </Row>
        <Row
          sub
          field
          off={!values.enabled}
          label="Album title matches"
          htmlFor="secondary-album-titles"
          help="Whole words split by | or commas, like christmas|xmas."
          warn={
            values.enabled && noRule
              ? 'No rule is on, so nothing goes to the secondary folder.'
              : undefined
          }
        >
          <TextField
            id="secondary-album-titles"
            wide
            placeholder="christmas|xmas"
            value={titles.value}
            onChange={(event) => titles.onChange(event.target.value)}
          />
        </Row>
        <Row
          sub
          off={!values.enabled}
          label="Albums you already have"
          help="Move the albums in your music library folder that match to the secondary folder. You see the list first."
          more="An album already in your library stays where it is when more of its tracks download, so it is never split. This is how you move it."
        >
          <Btn
            small
            id="secondary-preview"
            busy={plan.busy}
            disabled={!values.enabled}
            onClick={() => void move(false)}
          >
            Preview
          </Btn>
          {toMove ? (
            <Btn
              small
              kind="primary"
              id="secondary-move"
              busy={plan.busy}
              onClick={() => void confirmMove()}
            >
              Move {toMove}
            </Btn>
          ) : null}
        </Row>
        <div className={s.rowSay} id="secondary-plan" role="status" hidden={!plan.say}>
          {plan.say}
          {plan.items && plan.items.length ? (
            <ul>
              {plan.items.slice(0, 50).map((item) => (
                <li key={item.album_id}>
                  {item.artist}: {item.title} ({WHY[item.why] || item.why})
                  {item.action === 'move' || item.action === 'moved' ? '' : `: ${item.action}`}
                </li>
              ))}
              {plan.items.length > 50 ? <li>and {plan.items.length - 50} more</li> : null}
            </ul>
          ) : null}
        </div>
      </Card>
    </>
  );
}
