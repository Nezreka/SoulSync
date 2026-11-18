/**
 * People: the hero sentence with the counts, search and filter chips,
 * Invite and Add person (the existing flows), and one row per person with
 * what they can actually do in each category, from their effective map.
 * Profiles the migration left on their old settings are flagged "Needs a
 * look" with what it found, and open the editor like any other row.
 */

import { type KeyboardEvent, useMemo, useState } from 'react';

import type { PeopleData } from './data';
import type { QualityOptions } from './types';

import { Icon } from './icons';
import {
  type Cell,
  type Filter,
  type PersonRow,
  changeCount,
  countPeople,
  filterPeople,
  heroParts,
  isAdmin,
  lookReasons,
  metaLine,
  needsLook,
  numberWord,
  personCells,
} from './model';
import { type EditorHooks, openAddPerson, openInviteMaker, openPersonEditor } from './open-editor';
import css from './people.module.css';

function Avatar({ row }: { row: PersonRow }) {
  const p = row.person;
  return (
    <span
      className={css.av}
      style={{ ['--av' as string]: p.avatar_color || undefined }}
      aria-hidden="true"
    >
      {p.avatar_url ? <img src={p.avatar_url} alt="" /> : (p.name || '?').charAt(0).toUpperCase()}
    </span>
  );
}

function StateCell({ cell, hide }: { cell: Cell; hide?: string }) {
  const tone =
    cell.tone === 'dl'
      ? css.dl
      : cell.tone === 'ask'
        ? css.ask
        : cell.tone === 'plain'
          ? css.plain
          : css.off;
  return (
    <div className={`${css.cell} ${hide ?? ''}`} role="cell">
      <span className={`${css.st} ${tone}`}>
        <i aria-hidden="true" />
        {cell.text}
      </span>
      {cell.sub ? <div className={css.cellSub}>{cell.sub}</div> : null}
    </div>
  );
}

function FollowsChip({ row }: { row: PersonRow }) {
  if (isAdmin(row)) return <span className={`${css.tpl} ${css.tplAdmin}`}>Admin</span>;
  if (row.failed || !row.perms)
    return <span className={`${css.tpl} ${css.tplLook}`}>Unreadable</span>;
  if (row.perms.mode === 'legacy') {
    return <span className={`${css.tpl} ${css.tplLook}`}>Needs a look</span>;
  }
  const n = changeCount(row);
  return (
    <span className={css.tpl}>
      {row.perms.template?.name ?? 'Template'}
      {n ? (
        <b>
          · {n} {n === 1 ? 'change' : 'changes'}
        </b>
      ) : null}
    </span>
  );
}

function PersonLine({
  row,
  meId,
  quality,
  hooks,
}: {
  row: PersonRow;
  meId: number | null;
  quality: QualityOptions | null;
  hooks?: EditorHooks;
}) {
  const p = row.person;
  const open = () => void openPersonEditor(p, meId, hooks);
  const key = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      open();
    }
  };
  let body;
  if (isAdmin(row)) {
    body = (
      <div className={`${css.cell} ${css.span5} ${css.hideSm}`} role="cell">
        <span className={css.t2}>Everything, including settings and people</span>
      </div>
    );
  } else if (p.disabled) {
    body = (
      <div className={`${css.cell} ${css.span5} ${css.hideSm}`} role="cell">
        <span className={css.t2}>Turned off. Everything of theirs is kept.</span>
      </div>
    );
  } else if (!row.perms) {
    body = (
      <div className={`${css.cell} ${css.span5} ${css.hideSm}`} role="cell">
        <span className={css.t2}>Couldn&apos;t read what they can do. Open them to check.</span>
      </div>
    );
  } else {
    const cells = personCells(row.perms.effective, quality, row.asks ?? null);
    body = (
      <>
        <StateCell cell={cells.music} hide={css.hideSm} />
        <StateCell cell={cells.movies} hide={css.hideSm} />
        <StateCell cell={cells.tv} hide={css.hideSm} />
        <StateCell cell={cells.books} hide={`${css.hideSm} ${css.hideMd}`} />
        <StateCell cell={cells.requests} hide={`${css.hideSm} ${css.hideMd}`} />
      </>
    );
  }
  return (
    <div
      className={`${css.prow} ${p.disabled ? css.prowOff : ''}`}
      role="row"
      tabIndex={0}
      aria-label={`${p.name}: open their access`}
      onClick={open}
      onKeyDown={key}
    >
      <div className={css.who} role="cell">
        <Avatar row={row} />
        <div style={{ minWidth: 0 }}>
          <div className={css.t1}>
            {p.name}
            {p.disabled ? <span className={css.offPill}>Off</span> : null}
          </div>
          <div className={css.t2}>{metaLine(p, meId)}</div>
        </div>
      </div>
      <div role="cell">
        <FollowsChip row={row} />
      </div>
      {body}
      <span className={css.chev} aria-hidden="true">
        <Icon name="chev" />
      </span>
    </div>
  );
}

export function PeopleHome({
  data,
  meId,
  hooks,
}: {
  data: PeopleData;
  meId: number | null;
  hooks?: EditorHooks;
}) {
  const [filter, setFilter] = useState<Filter>('everyone');
  const [query, setQuery] = useState('');
  const counts = useMemo(() => countPeople(data.people), [data.people]);
  const hero = heroParts(counts, data.waiting);
  const flagged = data.people.filter(needsLook);
  const rows = filterPeople(data.people, filter, query);
  const chips: Array<[Filter, string, number | null]> = [
    ['everyone', 'Everyone', null],
    ['admins', 'Admins', null],
    ['changes', 'Has changes', null],
    ['off', 'Turned off', null],
  ];
  if (flagged.length) chips.push(['look', 'Needs a look', flagged.length]);

  return (
    <>
      <div className={css.hero}>
        <p className={css.heroLine}>
          {hero.map((part, i) =>
            part.em ? <em key={i}>{part.text}</em> : <span key={i}>{part.text}</span>,
          )}
        </p>
        <div className={css.stats}>
          <div className={css.stat}>
            <div className={css.statN}>{counts.members}</div>
            <div className={css.statL}>{counts.members === 1 ? 'Member' : 'Members'}</div>
          </div>
          <div className={css.stat}>
            <div className={css.statN}>{counts.admins}</div>
            <div className={css.statL}>{counts.admins === 1 ? 'Admin' : 'Admins'}</div>
          </div>
          {data.waiting != null ? (
            <div className={css.stat}>
              <div className={css.statN}>{data.waiting}</div>
              <div className={css.statL}>Waiting</div>
            </div>
          ) : null}
        </div>
      </div>

      {flagged.length ? (
        <div className={css.look} role="note">
          <Icon name="warn" />
          <div className={css.lookBody}>
            <b>
              {flagged.length === 1
                ? `${flagged[0].person.name} needs a look.`
                : `${numberWord(flagged.length, true)} people need a look.`}
            </b>{' '}
            When people moved to templates, {flagged.length === 1 ? 'their' : 'these'} old settings
            couldn&apos;t be said exactly with a template, so nothing was changed: they still run on
            them, under the global policy. Open {flagged.length === 1 ? 'them' : 'each one'} to pick
            a template.
            <ul className={css.lookList}>
              {flagged.map((row) => (
                <li key={row.person.id}>
                  <b>{row.person.name}</b>: {lookReasons(row.perms?.needs_look ?? []).join(' ')}
                </li>
              ))}
            </ul>
          </div>
          {flagged.length > 1 && filter !== 'look' ? (
            <button
              type="button"
              className={`${css.btn} ${css.secondary}`}
              onClick={() => setFilter('look')}
            >
              Show them
            </button>
          ) : null}
        </div>
      ) : null}

      <div className={css.bar}>
        <label className={css.search}>
          <Icon name="search" />
          <span className={css.srOnly}>Find someone</span>
          <input
            type="search"
            placeholder="Find someone"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoComplete="off"
          />
        </label>
        <div className={css.inline} role="group" aria-label="Show">
          {chips.map(([id, label, n]) => (
            <button
              key={id}
              type="button"
              className={`${css.chip} ${id === 'look' ? css.chipWarn : ''}`}
              aria-pressed={filter === id}
              onClick={() => setFilter(id)}
            >
              {label}
              {n ? <span className={css.count}>{n}</span> : null}
            </button>
          ))}
        </div>
        <span className={css.spacer} />
        <button
          type="button"
          className={`${css.btn} ${css.secondary}`}
          onClick={() => {
            if (!openInviteMaker())
              window.showToast?.("Invites aren't ready yet. Reload the page.", 'error');
          }}
        >
          Invite
        </button>
        <button
          type="button"
          className={`${css.btn} ${css.primary}`}
          onClick={() => {
            if (!openAddPerson())
              window.showToast?.("Adding people isn't ready yet. Reload the page.", 'error');
          }}
        >
          <Icon name="plus" />
          Add person
        </button>
      </div>

      <div className={css.list} role="table" aria-label="People">
        <div className={`${css.prow} ${css.prowHead}`} role="row">
          <span role="columnheader">Person</span>
          <span role="columnheader">Follows</span>
          <span role="columnheader" className={css.hideSm}>
            Music
          </span>
          <span role="columnheader" className={css.hideSm}>
            Movies
          </span>
          <span role="columnheader" className={css.hideSm}>
            TV
          </span>
          <span role="columnheader" className={`${css.hideSm} ${css.hideMd}`}>
            Books
          </span>
          <span role="columnheader" className={`${css.hideSm} ${css.hideMd}`}>
            Requests
          </span>
          <span />
        </div>
        {rows.length ? (
          rows.map((row) => (
            <PersonLine
              key={row.person.id}
              row={row}
              meId={meId}
              quality={data.quality}
              hooks={hooks}
            />
          ))
        ) : (
          <div className={css.empty} role="row">
            <span role="cell">
              {query ? `Nobody here matches "${query.trim()}".` : 'Nobody here matches that.'}
            </span>
          </div>
        )}
      </div>
      <div className={css.legend} aria-hidden="true">
        <span className={`${css.st} ${css.dl}`}>
          <i />
          Gets it right away
        </span>
        <span className={`${css.st} ${css.ask}`}>
          <i />
          Asks you first
        </span>
        <span className={`${css.st} ${css.off}`}>
          <i />
          Can&apos;t use it
        </span>
        <span>Click someone to open their access.</span>
      </div>
    </>
  );
}
