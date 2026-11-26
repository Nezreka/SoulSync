/**
 * the "this server" sections, admins only: setup and libraries, downloads,
 * the live guide's commercial breaks, and reported issues (which a member
 * also sees, as their own reports).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import type { CommercialEra, CommercialMode } from '../../-player.api';

import {
  bytes,
  offlineKeys,
  offlineSettingsQuery,
  saveOfflineSettings,
  type OfflineSettings,
} from '../../-lib/offline';
import {
  clearCommercialsCache,
  commercialsConfigQuery,
  commercialsStatusQuery,
  playerIssuesQuery,
  playerLibrariesQuery,
  saveCommercialsConfig,
  updatePlayerIssue,
  warmCommercials,
} from '../../-player.api';
import { ChevronIcon, GridIcon, MovieIcon, PencilIcon, PeopleIcon } from '../icons';
import { notify, notifyError } from '../notice';
import { openLibrarySetup, openPlayerSetup, playerSetupQuery, setupStep } from '../player-setup';
import { Card, Choices, Row, Switch, useSaved } from './kit';
import p from './settings.module.css';

const list = (names: string[]) =>
  names.length <= 2
    ? names.join(' and ')
    : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;

function Tile({
  icon,
  title,
  children,
  action,
  onOpen,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
  action: string;
  onOpen: () => void;
}) {
  return (
    <section className={p.tile} aria-label={title}>
      <span className={p.tileIcon} aria-hidden="true">
        {icon}
      </span>
      <b className={p.tileTitle}>{title}</b>
      <span className={p.tileText}>{children}</span>
      <button type="button" className={p.tileGo} onClick={onOpen} data-nav>
        {action}
        <ChevronIcon />
      </button>
    </section>
  );
}

export function SetupSection() {
  const libraries = useQuery(playerLibrariesQuery()).data ?? [];
  const setup = useQuery(playerSetupQuery()).data;
  const libraryNames = libraries.map((l) => l.name).filter(Boolean);
  // who has a library switched off, from setup's own answers
  const people = (setup?.profiles ?? []).filter((x) => !x.is_admin);
  const nameOf = new Map((setup?.libraries ?? []).map((l) => [String(l.id), l.name]));
  const limits = people
    .map((person) => ({
      name: person.name,
      off: Object.entries(setup?.hidden ?? {})
        .filter(([, ids]) => ids.includes(person.id))
        .map(([lib]) => nameOf.get(lib))
        .filter((n): n is string => Boolean(n)),
    }))
    .filter((x) => x.off.length);
  const hiddenPlaces = setup?.sidebar_hidden?.length ?? 0;
  return (
    <>
      <div className={p.tiles}>
        <Tile
          icon={<MovieIcon />}
          title="Libraries"
          action="Edit libraries"
          onOpen={openLibrarySetup}
        >
          {libraryNames.length
            ? `You have ${list(libraryNames)}. Add, remove, rename or regroup them; nothing on disk moves.`
            : 'Add, remove, rename or regroup your libraries; nothing on disk moves.'}
        </Tile>
        <Tile
          icon={<PeopleIcon />}
          title="Who sees what"
          action="Choose"
          onOpen={() => openPlayerSetup(setupStep('Who sees what'))}
        >
          {!setup
            ? 'Switch a library off for someone and it is gone for them everywhere.'
            : limits.length
              ? limits
                  .slice(0, 2)
                  .map((x) => `${x.name} can't see ${list(x.off)}.`)
                  .join(' ') + (limits.length > 2 ? ` ${limits.length - 2} more people too.` : '')
              : people.length
                ? 'Everyone sees every library.'
                : 'Only admins so far. They always see everything.'}
        </Tile>
        <Tile
          icon={<GridIcon />}
          title="Sidebar for everyone"
          action="Arrange"
          onOpen={() => openPlayerSetup(setupStep('Sidebar'))}
        >
          The order everyone starts with
          {hiddenPlaces
            ? `, ${hiddenPlaces} ${hiddenPlaces === 1 ? 'place' : 'places'} left out`
            : ''}
          . Each person can still change theirs under Sidebar.
        </Tile>
        <Tile
          icon={<PencilIcon />}
          title="Server name"
          action="Rename"
          onOpen={() => openPlayerSetup(setupStep('Welcome'))}
        >
          {setup?.server_name ? (
            <span className={p.tileValue}>{setup.server_name}</span>
          ) : (
            'What the player calls this server.'
          )}
        </Tile>
      </div>
      <Card>
        <Row
          label="Libraries, people and the server"
          help="Every step of setup, from the start. Nothing changes until you save."
        >
          <button type="button" className={p.actionLink} onClick={() => openPlayerSetup()} data-nav>
            <span>Open player setup</span>
            <ChevronIcon />
          </button>
        </Row>
      </Card>
    </>
  );
}

const CONVERSIONS = [1, 2, 3, 4, 6, 8];
const CACHE_GB = [10, 25, 50, 100, 250, 500];
const withNow = (choices: number[], v: number) =>
  choices.includes(v) ? choices : [...choices, v].sort((a, b) => a - b);

/** what the server does for downloads: smaller copies, how many at once, how much room */
export function DownloadsSection() {
  const queryClient = useQueryClient();
  const onSaved = useSaved();
  const q = useQuery(offlineSettingsQuery());
  const save = useMutation({
    mutationFn: (changes: Partial<OfflineSettings>) => saveOfflineSettings(changes),
    onSuccess: (out) => {
      queryClient.setQueryData(offlineKeys.settings, (old: typeof q.data) =>
        old ? { ...old, settings: out.settings } : old,
      );
      onSaved();
    },
    onError: notifyError("Couldn't save that setting"),
  });
  const now = q.data?.settings;
  if (q.isPending) return <p className={p.quiet}>Loading…</p>;
  if (!q.data?.is_admin || !now) return <p className={p.quiet}>Only an admin can change these.</p>;
  return (
    <Card>
      <Row
        label="Members may ask for smaller copies"
        help="1080p, 720p, 480p or a lower bitrate. Off: members download originals only. Admins always can."
      >
        <Switch
          label="Members may ask for smaller copies"
          on={now.allow_conversion_for_members}
          busy={save.isPending}
          onChange={(v) => save.mutate({ allow_conversion_for_members: v })}
        />
      </Row>
      <Row stacked label="Copies made at once" help="Each one keeps a CPU core or the GPU busy.">
        <Choices
          label="Copies made at once"
          options={withNow(CONVERSIONS, now.max_conversions).map((n) => ({
            value: n,
            label: String(n),
          }))}
          value={now.max_conversions}
          disabled={save.isPending}
          onPick={(n) => save.mutate({ max_conversions: n })}
        />
      </Row>
      <Row
        stacked
        label="Room for smaller copies"
        help={`${bytes(q.data.cache_used_bytes) || 'Nothing'} in use. When it's full, the copies used longest ago go first.`}
      >
        <Choices
          label="Room for smaller copies"
          options={withNow(CACHE_GB, now.cache_gb).map((n) => ({ value: n, label: `${n} GB` }))}
          value={now.cache_gb}
          disabled={save.isPending}
          onPick={(n) => save.mutate({ cache_gb: n })}
        />
      </Row>
    </Card>
  );
}

const ERAS: { value: CommercialEra; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: '70s', label: '70s' },
  { value: '80s', label: '80s' },
  { value: '90s', label: '90s' },
  { value: '2000s', label: '2000s' },
  { value: 'kids', label: 'Kids' },
  { value: 'mix', label: 'Mix' },
];
const MODES: { value: CommercialMode; label: string }[] = [
  { value: 'cable', label: 'Classic cable' },
  { value: 'short', label: 'Short (1–2 spots)' },
];

export function LiveGuideSection({ isAdmin }: { isAdmin: boolean }) {
  const queryClient = useQueryClient();
  const onSaved = useSaved();
  const commConfig = useQuery(commercialsConfigQuery());
  const commStatus = useQuery({
    ...commercialsStatusQuery(),
    refetchInterval: (q) => (q.state.data?.warming ? 3000 : false),
  });
  const saveComm = useMutation({
    mutationFn: (changes: { enabled?: boolean; era?: CommercialEra; mode?: CommercialMode }) =>
      saveCommercialsConfig(changes),
    onSuccess: (next) => {
      queryClient.setQueryData(commercialsConfigQuery().queryKey, next);
      void queryClient.invalidateQueries({ queryKey: ['player', 'commercials-status'] });
      onSaved();
    },
    onError: notifyError("Couldn't save the commercials setting"),
  });
  const [warming, setWarming] = useState(false);
  const [clearing, setClearing] = useState(false);
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['player', 'commercials-status'] }),
      queryClient.invalidateQueries({ queryKey: ['player', 'commercials-config'] }),
    ]);
  const st = commStatus.data;
  const managed = isAdmin ? '' : ' Managed by server admin.';
  return (
    <Card>
      <Row
        label="Include retro commercials in Live Guide"
        help={
          st?.warming
            ? 'Fetching retro spots from Archive.org in the background…'
            : st?.total_count
              ? `${st.total_count} retro spots ready (${st.cache_mb} / ${st.max_cache_mb ?? 500} MB cached).${managed}`
              : `Off by default. Caches vintage 15–60s spots by itself (max 500 MB).${managed}`
        }
      >
        <Switch
          label="Include retro commercials in Live Guide"
          on={commConfig.data?.enabled ?? false}
          busy={!isAdmin || !commConfig.data || saveComm.isPending}
          onChange={(v) => saveComm.mutate({ enabled: v })}
        />
      </Row>
      {commConfig.data?.enabled ? (
        <>
          <Row
            stacked
            label="Commercial era"
            help="Auto matches each show's release year (±4 years) or the channel's vibe."
          >
            <Choices
              label="Commercial era"
              options={ERAS}
              value={commConfig.data?.era ?? 'auto'}
              disabled={!isAdmin || saveComm.isPending}
              onPick={(era) => saveComm.mutate({ era })}
            />
          </Row>
          <Row
            stacked
            label="Break style"
            help="Classic cable plays 2–3 spots between shows, at most once every 5 minutes. Short plays 1–2 quick spots."
          >
            <Choices
              label="Break style"
              options={MODES}
              value={commConfig.data?.mode ?? 'cable'}
              disabled={!isAdmin || saveComm.isPending}
              onPick={(mode) => saveComm.mutate({ mode })}
            />
          </Row>
          {isAdmin ? (
            <Row
              label="Commercial cache"
              help="Fetch fresh vintage spots from the Internet Archive (up to 500 MB), or clear the server's cache."
            >
              <div className={p.buttons}>
                <button
                  type="button"
                  className={p.pill}
                  data-tone="go"
                  disabled={warming || st?.warming}
                  onClick={() => {
                    setWarming(true);
                    void warmCommercials(commConfig.data?.era ?? 'mix')
                      .then((res) => {
                        if (res.started === false && !res.warming) {
                          notify(
                            `Your commercial space is full: ${res.total_count} spots. Clear it to fetch a fresh set.`,
                          );
                        }
                        return refresh();
                      })
                      .catch(notifyError("Couldn't fetch spots right now"))
                      .finally(() => setWarming(false));
                  }}
                  data-nav
                >
                  {warming || st?.warming ? 'Fetching…' : 'Fetch spots now'}
                </button>
                <button
                  type="button"
                  className={p.pill}
                  disabled={clearing || !(st?.cached_count ?? 0)}
                  onClick={() => {
                    setClearing(true);
                    void clearCommercialsCache()
                      .then(refresh)
                      .catch(notifyError("Couldn't clear the spots"))
                      .finally(() => setClearing(false));
                  }}
                  data-nav
                >
                  {clearing ? 'Clearing…' : 'Clear cache'}
                </button>
              </div>
            </Row>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

const STATUS_WORD = { open: 'Open', resolved: 'Resolved', dismissed: 'Dismissed' } as const;

export function IssuesSection() {
  const queryClient = useQueryClient();
  const onSaved = useSaved();
  const issuesQ = useQuery(playerIssuesQuery());
  const resolve = useMutation({
    mutationFn: ({ id, status }: { id: number; status: 'resolved' | 'dismissed' | 'open' }) =>
      updatePlayerIssue(id, { status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['player', 'issues'] });
      onSaved();
    },
    onError: notifyError("Couldn't update that report"),
  });
  const data = issuesQ.data;
  if (!data?.issues.length) {
    return (
      <div className={p.empty}>
        <b>{issuesQ.isPending ? 'Loading reports…' : 'Nothing reported'}</b>
        {issuesQ.isPending ? null : (
          <span>
            Use the flag button on any movie, episode, YouTube video, track, podcast or audiobook to
            report a broken file.
          </span>
        )}
      </div>
    );
  }
  return (
    <ul className={p.issues}>
      {data.issues.slice(0, 20).map((iss) => (
        <li key={iss.id} className={p.issue} data-status={iss.status}>
          <div className={p.issueHead}>
            <div className={p.issueTitle}>
              <strong>{iss.title}</strong>
              <span className={p.issueKind}>
                {iss.item_type} · {iss.category}
              </span>
              <span className={p.issueStatus} data-status={iss.status}>
                {STATUS_WORD[iss.status] ?? iss.status}
              </span>
            </div>
            {data.can_manage && iss.status === 'open' ? (
              <div className={p.buttons}>
                <button
                  type="button"
                  className={p.pill}
                  data-tone="go"
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate({ id: iss.id, status: 'resolved' })}
                  data-nav
                >
                  Resolve
                </button>
                <button
                  type="button"
                  className={p.pill}
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate({ id: iss.id, status: 'dismissed' })}
                  data-nav
                >
                  Dismiss
                </button>
              </div>
            ) : null}
          </div>
          {iss.note ? <p className={p.issueNote}>{iss.note}</p> : null}
          {iss.profile_name || iss.file_path ? (
            <div className={p.issueMeta}>
              {iss.profile_name ? `Reported by ${iss.profile_name}` : ''}
              {iss.profile_name && iss.file_path ? ' · ' : ''}
              {iss.file_path ? <span className={p.issuePath}>{iss.file_path}</span> : null}
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
