/**
 * Quality > Music (settings-bic phase 3, mockup 04; the state is what
 * SETTINGS_PORT.md step 6 ported): the profiles as cards across the top (each
 * card's ⋯ menu holds Set as default, Auto-Import, Rename, Save now and
 * Delete; the Manage modal that repeated them went), the priority list on the
 * shared order list with the upgrade cutoff drawn in it, upgrades and
 * searching, what is kept after a download, and the rarer checks behind More.
 *
 * Every control but "Verify FLAC decode" belongs to the profile on screen.
 * An edit is saved to that profile (POST /api/quality-profile/custom/<id>/update)
 * through the settings store, so autosave, the autosave switch and the save
 * button treat it like any other change. The server copies the default
 * profile into config on its own.
 */

import clsx from 'clsx';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../host';

import {
  Btn,
  Card,
  Icon,
  More,
  Note,
  NumberField,
  OrderList,
  Row,
  SectionLabel,
  Segmented,
  Select,
  Switch,
  Tag,
} from '../ui';
import s from '../ui/settings.module.css';
import {
  type AddFields,
  type Bundle,
  EMPTY_ADD,
  EMPTY_LADDER,
  type Extras,
  type Ladder,
  PRESET_LABELS,
  type ProfileRow,
  type RankedTarget,
  SAVE_KEY,
  type ServerProfile,
  addTargets,
  call,
  clampCutoff,
  collectBundle,
  defaultProfileId,
  extrasFrom,
  extrasFromConfig,
  ladderFrom,
  lossyBitrateFor,
  normalizeProfileId,
  postJson,
  profileSummary,
  RT_LOSSLESS_FORMATS,
  rtLabel,
  rtSelectionIsLossless,
  withTargets,
} from './quality';
import { QualityPage } from './quality/quality-page';

const toast = (message: string, type: string) => window.showToast?.(message, type);

function confirm(options: {
  title: string;
  message: string;
  confirmText: string;
  destructive?: boolean;
}) {
  return window.showConfirmDialog?.(options) ?? Promise.resolve(false);
}

/** every other quality profile picker in the app reads a cache in
 *  shared-helpers.js; a change to the list here has to drop it (R2-11) */
function dropProfileCache() {
  (
    window as Window & { invalidatePlaylistQualityProfiles?: () => void }
  ).invalidatePlaylistQualityProfiles?.();
}

/** the profile row's name, as a text box while it is being renamed */
function RenameInput({
  name,
  onCommit,
  onCancel,
}: {
  name: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const settled = useRef(false);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  const commit = () => {
    if (settled.current) return;
    settled.current = true;
    onCommit(ref.current?.value ?? '');
  };
  return (
    <input
      ref={ref}
      type="text"
      className={clsx(s.input, s.qpRename)}
      aria-label="New name"
      defaultValue={name}
      // the name sits in the row's clickable text: typing here must not open it
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          commit();
        } else if (event.key === 'Escape') {
          event.preventDefault();
          if (settled.current) return;
          settled.current = true;
          onCancel();
        }
      }}
      onBlur={commit}
    />
  );
}

/** "+ New profile" as a text box */
function NewProfileInput({
  onCommit,
  onCancel,
}: {
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const settled = useRef(false);
  useEffect(() => ref.current?.focus(), []);
  const commit = () => {
    if (settled.current) return;
    const name = (ref.current?.value ?? '').trim();
    if (!name) {
      onCancel();
      return;
    }
    settled.current = true;
    onCommit(name);
  };
  const cancel = () => {
    if (settled.current) return;
    settled.current = true;
    onCancel();
  };
  return (
    <div className={s.field}>
      <input
        ref={ref}
        type="text"
        className={s.input}
        aria-label="New profile name"
        id="qp-profile-new-input"
        placeholder="Profile name"
        autoComplete="off"
        spellCheck={false}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            commit();
          } else if (event.key === 'Escape') {
            event.preventDefault();
            cancel();
          }
        }}
        onBlur={cancel}
      />
    </div>
  );
}

/** the Quality tab: its page, one pill per kind (quality/quality-page.tsx) */
export function QualityTab(props: SettingsTabProps) {
  return <QualityPage {...props} />;
}

export function MusicQualityTab({ store }: SettingsTabProps) {
  const state = useSyncExternalStore(store.subscribe, store.getState);

  const [ladder, setLadderState] = useState<Ladder>(EMPTY_LADDER);
  // null until a profile arrives: the controls show config, as the page did
  const [extrasState, setExtrasState] = useState<Extras | null>(null);
  // a profile has arrived. Before that there is nothing to save an edit to
  // (the old page saved an empty target list over the profile here)
  const [ready, setReady] = useState(false);
  const [rows, setRowsState] = useState<ProfileRow[]>([]);
  // the list has arrived once: before that the panel is empty, not "No profiles yet."
  const [rowsLoaded, setRowsLoaded] = useState(false);
  const [autoImportId, setAutoImportId] = useState<number | null>(null);
  // the non-default profile on screen; null is the active default
  const [editingId, setEditingIdState] = useState<number | null>(null);
  const [add, setAdd] = useState<AddFields>(EMPTY_ADD);
  const [renaming, setRenaming] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  // the profile whose ⋯ menu is open
  const [menu, setMenu] = useState<number | null>(null);
  useEffect(() => {
    if (menu === null) return;
    const outside = (event: PointerEvent) => {
      if (!(event.target as Element | null)?.closest?.(`[data-profile-id="${menu}"]`))
        setMenu(null);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [menu]);

  const extras = extrasState ?? extrasFromConfig(state.settings);

  // the async actions read what is on screen when they finish, not when they began
  const now = useRef({ ladder, extras, rows, editingId, ready, autoImportId });
  now.current = { ladder, extras, rows, editingId, ready, autoImportId };

  const setLadder = (next: Ladder) => {
    now.current.ladder = next;
    setLadderState(next);
  };
  const setExtras = (next: Extras) => {
    now.current.extras = next;
    setExtrasState(next);
  };
  const setRows = (next: unknown) => {
    const list = Array.isArray(next) ? (next as ProfileRow[]) : [];
    now.current.rows = list;
    setRowsState(list);
    setRowsLoaded(true);
    dropProfileCache();
  };
  const setEditingId = (id: number | null) => {
    now.current.editingId = id;
    setEditingIdState(id);
  };

  const editingOther = () => {
    const { editingId: id, rows: list } = now.current;
    return id !== null && id !== defaultProfileId(list);
  };

  async function saveProfile(value: unknown): Promise<boolean> {
    const { target, bundle } = value as { target: number | null; bundle: Bundle };
    try {
      const url =
        target == null ? '/api/quality-profile' : `/api/quality-profile/custom/${target}/update`;
      const data = await postJson(url, bundle);
      if (!data.success) {
        console.error('Failed to save quality profile:', data.error);
        return false;
      }
      if (Array.isArray(data.profiles)) setRows(data.profiles);
      return true;
    } catch (error) {
      console.error('Error saving quality profile:', error);
      return false;
    }
  }

  /** an edit: saved to the profile on screen now, whatever is opened next */
  function queueSave() {
    const { ladder: l, extras: e, editingId: id, rows: list, ready: ok } = now.current;
    if (!ok) return;
    const target = id ?? defaultProfileId(list);
    store.setExtra(
      `${SAVE_KEY}${target ?? 'default'}`,
      { target, bundle: collectBundle(l, e) },
      saveProfile,
    );
  }

  const editLadder = (change: (l: Ladder) => Ladder) => {
    setLadder(change(now.current.ladder));
    queueSave();
  };
  const editExtras = (patch: Partial<Extras>) => {
    setExtras({ ...now.current.extras, ...patch });
    queueSave();
  };

  /** a profile's settings onto the page (populateQualityProfileUI and,
   *  with `all`, applyFullQualityBundleToDom) */
  const show = (profile: ServerProfile, all: boolean) => {
    setLadder(ladderFrom(profile, now.current.ladder));
    if (all) setExtras(extrasFrom(profile, now.current.extras));
    // a profile on screen is one its edits save to, however it got there
    // (the first load failing included)
    now.current.ready = true;
    setReady(true);
  };

  async function loadRows() {
    try {
      const [profiles, autoImport] = await Promise.all([
        fetch('/api/quality-profile/custom'),
        fetch('/api/auto-import/settings').catch(() => null),
      ]);
      const data = (await profiles.json()) as { success?: boolean; profiles?: unknown };
      if (autoImport && autoImport.ok) {
        const ai = (await autoImport.json()) as { quality_profile_id?: unknown };
        const id = normalizeProfileId(ai.quality_profile_id);
        now.current.autoImportId = id;
        setAutoImportId(id);
      }
      if (data.success) setRows(data.profiles);
    } catch (error) {
      console.error('Error loading custom quality profiles:', error);
    }
  }

  // each visit to Settings loads the settings, and the active default with them
  useEffect(() => {
    if (!state.loads) return;
    let live = true;
    void (async () => {
      // an edit not saved yet (autosave off) stays on screen, and is saved
      // to its profile by the save button
      if (!store.hasExtra(SAVE_KEY)) {
        try {
          const data = await call('/api/quality-profile');
          if (live && data.success) {
            const profile = data.profile as ServerProfile;
            setEditingId(null);
            setLadder(ladderFrom(profile, now.current.ladder));
            setExtras(extrasFrom(profile, extrasFromConfig(store.getState().settings)));
            now.current.ready = true;
            setReady(true);
          }
        } catch (error) {
          console.error('Error loading quality profile:', error);
        }
      }
      if (live) await loadRows();
    })();
    return () => {
      live = false;
    };
  }, [state.loads]); // eslint-disable-line react-hooks/exhaustive-deps

  async function applyPreset(name: string) {
    // a queued save of the old list must not land after the switch
    store.dropExtras(SAVE_KEY);
    const label = PRESET_LABELS[name] || name;
    // a profile other than the default on screen: the preset route always
    // writes the default, so load the preset here and save it to this one
    if (editingOther()) {
      try {
        const data = await call('/api/quality-profile/presets');
        const presets = data.presets as Record<string, ServerProfile> | undefined;
        const preset = data.success ? presets?.[name] : null;
        if (!preset) {
          toast(`Failed to load '${name}' preset`, 'error');
          return;
        }
        // the search strategy and upgrade rules are not part of a preset
        const l = now.current.ladder;
        show(
          {
            ...preset,
            search_mode: l.searchMode,
            rank_candidates_by_quality: l.rankCandidates,
            upgrade_policy: l.upgradePolicy,
            upgrade_cutoff_index: clampCutoff(l.cutoff, l.targets),
            release_import_mode: l.releaseImportMode,
          },
          false,
        );
        queueSave();
        toast(`Applied '${label}' to this profile`, 'success');
      } catch (error) {
        console.error('Error loading quality preset:', error);
        toast('Failed to load preset', 'error');
      }
      return;
    }
    try {
      const data = await call(`/api/quality-profile/preset/${name}`, { method: 'POST' });
      if (data.success) {
        show(data.profile as ServerProfile, false);
        await loadRows();
        toast(`Switched to '${label}'`, 'success');
      } else {
        toast(`Failed to apply preset: ${data.error}`, 'error');
      }
    } catch (error) {
      console.error('Error applying quality preset:', error);
      toast('Failed to apply preset', 'error');
    }
  }

  async function resetPreset() {
    // only the three presets have factory settings, and the reset route
    // always writes the live default
    if (editingOther()) {
      toast(
        'Reset to defaults only applies to the live default profile — this one has no factory preset to reset to',
        'info',
      );
      return;
    }
    const name = now.current.ladder.preset;
    if (!name || !(name in PRESET_LABELS)) {
      toast('No preset selected to reset', 'info');
      return;
    }
    store.dropExtras(SAVE_KEY);
    try {
      const data = await call(`/api/quality-profile/preset/${name}/reset`, { method: 'POST' });
      if (data.success) {
        show(data.profile as ServerProfile, false);
        await loadRows();
        toast(`Reset '${PRESET_LABELS[name] || name}' to defaults`, 'success');
      } else {
        toast(`Failed to reset preset: ${data.error}`, 'error');
      }
    } catch (error) {
      console.error('Error resetting quality preset:', error);
      toast('Failed to reset preset', 'error');
    }
  }

  // ── the profile panel ──

  async function previewProfile(id: number) {
    try {
      const data = await call(`/api/quality-profile/custom/${id}`);
      if (!data.success) {
        toast(`Failed to load profile: ${data.error}`, 'error');
        return;
      }
      const profile = data.profile as ServerProfile;
      setEditingId(profile.is_default ? null : id);
      show(profile, true);
    } catch (error) {
      console.error('Error previewing quality profile:', error);
      toast('Failed to load profile', 'error');
    }
  }

  async function setDefault(id: number, name: string) {
    const yes = await confirm({
      title: 'Set Active Profile',
      message: `Make '${name}' the active default profile? New downloads and anything without its own profile follow it immediately — existing Wishlist items keep the profile they were queued with.`,
      confirmText: 'Set Active',
    });
    if (!yes) return;
    try {
      const data = await call(`/api/quality-profile/custom/${id}/apply`, { method: 'POST' });
      if (data.success) {
        setEditingId(null);
        show(data.profile as ServerProfile, true);
        await loadRows();
        toast(`Now using '${name}' (all Quality-page settings updated)`, 'success');
      } else {
        toast(`Failed to apply profile: ${data.error}`, 'error');
      }
    } catch (error) {
      console.error('Error applying custom quality profile:', error);
      toast('Failed to apply profile', 'error');
    }
  }

  async function rename(profile: ProfileRow, value: string) {
    const name = value.trim();
    if (!name || name === profile.name) {
      setRenaming(null);
      await loadRows();
      return;
    }
    try {
      const data = await postJson(`/api/quality-profile/custom/${profile.id}`, { name }, 'PUT');
      if (data.success) {
        setRows(data.profiles);
        setRenaming(null);
        toast(`Renamed to '${name}'`, 'success');
      } else {
        toast(`Failed to rename: ${data.error}`, 'error');
        setRenaming(null);
        await loadRows();
      }
    } catch (error) {
      console.error('Error renaming quality profile:', error);
      toast('Failed to rename profile', 'error');
      setRenaming(null);
      await loadRows();
    }
  }

  async function saveAsNew(name: string) {
    try {
      const bundle = collectBundle(now.current.ladder, now.current.extras);
      const data = await postJson('/api/quality-profile/custom', { ...bundle, name });
      if (data.success) {
        setRows(data.profiles);
        // the page already shows what was saved: it is the profile on screen now
        setEditingId(typeof data.id === 'number' ? data.id : null);
        toast(`Saved profile '${name}'`, 'success');
      } else {
        toast(`Failed to save profile: ${data.error}`, 'error');
      }
    } catch (error) {
      console.error('Error saving custom quality profile:', error);
      toast('Failed to save profile', 'error');
    }
    setAdding(false);
  }

  async function saveNow(id: number, name: string) {
    const yes = await confirm({
      title: 'Update Profile',
      message: `Update '${name}' with the current page settings? This overwrites what was saved before.`,
      confirmText: 'Update',
    });
    if (!yes) return;
    try {
      const bundle = collectBundle(now.current.ladder, now.current.extras);
      const data = await postJson(`/api/quality-profile/custom/${id}/update`, bundle);
      if (data.success) {
        setRows(data.profiles);
        toast(`Updated '${name}'`, 'success');
      } else {
        toast(`Failed to update profile: ${data.error}`, 'error');
      }
    } catch (error) {
      console.error('Error updating custom quality profile:', error);
      toast('Failed to update profile', 'error');
    }
  }

  async function remove(id: number, name: string) {
    const yes = await confirm({
      title: 'Delete Profile',
      message: `Delete '${name}'? This cannot be undone. Anything still assigned to it (Wishlist items, Auto-Import) automatically falls back to your active profile.`,
      confirmText: 'Delete',
      destructive: true,
    });
    if (!yes) return;
    try {
      const data = await call(`/api/quality-profile/custom/${id}`, { method: 'DELETE' });
      if (data.success) {
        // the profile on screen is gone: back to the live default, as on a fresh load
        if (now.current.editingId === id) setEditingId(null);
        // and an edit of it not saved yet has nowhere to go: it would fail
        // (404) on every save after, and hold every later visit's load
        store.dropExtra(`${SAVE_KEY}${id}`);
        // deleting may have cleared Auto-Import or made another profile the
        // default: both show on the rows, so they come from the server
        await loadRows();
        toast(`Deleted '${name}'`, 'success');
      } else {
        toast(`Failed to delete profile: ${data.error}`, 'error');
      }
    } catch (error) {
      console.error('Error deleting custom quality profile:', error);
      toast('Failed to delete profile', 'error');
    }
  }

  async function toggleAutoImport(id: number) {
    const assigned = now.current.autoImportId === id;
    try {
      const data = await postJson('/api/auto-import/settings', {
        quality_profile_id: assigned ? null : id,
      });
      if (!data.success) throw new Error(data.error || 'Failed to update Auto-Import');
      const next = assigned ? null : id;
      now.current.autoImportId = next;
      setAutoImportId(next);
      toast(
        assigned ? 'Auto-Import now uses the default profile' : 'Auto-Import profile updated',
        'success',
      );
    } catch (error) {
      console.error('Error updating Auto-Import quality profile:', error);
      toast('Failed to update Auto-Import profile', 'error');
    }
  }

  // ── the ranked list (the order list, as the download order) ──

  const targets = ladder.targets;
  /** a target's place in the list is its identity while it is in it */
  const targetId = (t: RankedTarget) =>
    JSON.stringify([t.format, t.bit_depth ?? 0, t.min_sample_rate ?? 0, t.min_bitrate ?? 0]);
  const byId = new Map(targets.map((t) => [targetId(t), t]));
  const reorderTargets = (order: string[]) =>
    editLadder((l) =>
      withTargets(
        l,
        order.map((id) => byId.get(id)).filter((t): t is RankedTarget => !!t),
      ),
    );

  const lossless = rtSelectionIsLossless(add.format);
  const cutoff = clampCutoff(ladder.cutoff, targets);
  const untilCutoff = ladder.upgradePolicy === 'until_cutoff';

  // ── the profiles ──

  const onScreen =
    editingId === null ? rows.find((p) => p.is_default) : rows.find((p) => p.id === editingId);
  const editingRow =
    editingId === null ? undefined : rows.find((p) => p.id === editingId && !p.is_default);
  const profileName = onScreen?.name ?? (rowsLoaded ? 'This profile' : '');

  const closeMenu = () => setMenu(null);
  const menuItem = (
    label: string,
    onSelect: () => void,
    opts: { danger?: boolean; disabled?: boolean } = {},
  ) => (
    <button
      type="button"
      role="menuitem"
      className={clsx(s.menuItem, opts.danger && s.menuDanger)}
      disabled={opts.disabled}
      onClick={() => {
        closeMenu();
        onSelect();
      }}
    >
      {label}
    </button>
  );

  const profileCard = (profile: ProfileRow) => {
    const isOnScreen = profile.id === onScreen?.id;
    const isAutoImport = profile.id === autoImportId;
    return (
      <div
        key={profile.id}
        className={clsx(s.qpCard, isOnScreen && s.qpCardOn)}
        data-profile-id={profile.id}
      >
        {renaming === profile.id ? (
          <div className={s.qpCardMain}>
            <RenameInput
              name={profile.name}
              onCommit={(value) => void rename(profile, value)}
              onCancel={() => {
                setRenaming(null);
                void loadRows();
              }}
            />
          </div>
        ) : (
          <button
            type="button"
            className={s.qpCardMain}
            aria-pressed={isOnScreen}
            title={
              profile.is_default
                ? "View this profile's settings (it's already the active default)"
                : `View and edit '${profile.name}'. Opening it does not make it the active one.`
            }
            onClick={() => void previewProfile(profile.id)}
          >
            <span className={s.qpCardName} data-profile-name="">
              {profile.name}
            </span>
            <span className={s.qpCardSub}>{profileSummary(profile)}</span>
            {profile.is_default || isAutoImport ? (
              <span className={s.qpCardTags}>
                {profile.is_default ? (
                  <Tag accent>
                    <span title="Used by normal downloads and Wishlist items right now">
                      Downloads
                    </span>
                  </Tag>
                ) : null}
                {isAutoImport ? <Tag>Auto-Import</Tag> : null}
              </span>
            ) : null}
          </button>
        )}
        <span className={s.qpCardMore}>
          <Btn
            kind="ghost"
            small
            iconOnly
            icon="more"
            aria-label={`More for ${profile.name}`}
            aria-haspopup="menu"
            aria-expanded={menu === profile.id}
            onClick={() => setMenu(menu === profile.id ? null : profile.id)}
          />
          {menu === profile.id ? (
            <>
              <div className={s.menuScrim} onClick={closeMenu} />
              <div
                className={s.menu}
                role="menu"
                aria-label={`${profile.name}: what to do`}
                onKeyDown={(event) => {
                  if (event.key === 'Escape') {
                    event.stopPropagation();
                    closeMenu();
                  }
                }}
              >
                <div className={s.menuGrab} aria-hidden="true" />
                {menuItem(
                  profile.is_default ? 'The active default' : 'Set as default',
                  () => void setDefault(profile.id, profile.name),
                  { disabled: !!profile.is_default },
                )}
                {menuItem(
                  isAutoImport ? 'Stop using for Auto-Import' : 'Use for Auto-Import',
                  () => void toggleAutoImport(profile.id),
                )}
                {menuItem('Rename', () => setRenaming(profile.id))}
                {menuItem('Save this page to it now', () => void saveNow(profile.id, profile.name))}
                {rows.length > 1 ? (
                  <>
                    <hr className={s.menuHr} />
                    {menuItem('Delete', () => void remove(profile.id, profile.name), {
                      danger: true,
                    })}
                  </>
                ) : null}
              </div>
            </>
          ) : null}
        </span>
      </div>
    );
  };

  const presetButton = (name: string, title: string) => (
    <Btn
      small
      kind={ladder.preset === name ? undefined : 'ghost'}
      aria-pressed={ladder.preset === name}
      title={title}
      onClick={() => void applyPreset(name)}
    >
      {PRESET_LABELS[name]}
    </Btn>
  );

  return (
    <div data-music-quality="">
      <SectionLabel
        title="Quality profiles"
        hint="Click one to edit it. Editing never makes it the active one."
      />
      <div className={s.qpCards} id="qp-profile-list">
        {!rowsLoaded ? null : rows.length ? (
          rows.map(profileCard)
        ) : (
          <p className={s.listEmpty}>No profiles yet.</p>
        )}
        <div className={s.qpCardNew} id="qp-profile-new">
          {adding ? (
            <NewProfileInput
              onCommit={(name) => void saveAsNew(name)}
              onCancel={() => setAdding(false)}
            />
          ) : (
            <button
              type="button"
              className={s.qpNewBtn}
              id="qp-profile-new-btn"
              title="Save what is on this page as a new profile"
              onClick={() => setAdding(true)}
            >
              <Icon name="plus" size={15} />
              New profile
            </button>
          )}
        </div>
      </div>
      <div id="qp-editing-banner" hidden={!editingRow}>
        <Note info>
          Editing <strong>{editingRow?.name ?? 'this profile'}</strong>, not the active default.
          Changes autosave to this profile; the save status at the top saves it right away too.
        </Note>
      </div>

      <SectionLabel title="Quality priority" hint="First is most preferred. Drag to reorder." />
      <Card
        id="quality-profile-section"
        title={`What ${profileName || 'this profile'} accepts`}
        hint="Quick presets replace this list. Edits are kept per preset."
        actions={
          <>
            {presetButton('audiophile', 'FLAC only, strict size constraints')}
            {presetButton('balanced', 'FLAC preferred, MP3 fallback')}
            {presetButton('space_saver', 'MP3 preferred, smaller sizes')}
            <Btn
              small
              kind="ghost"
              icon="undo"
              title="Restore the selected preset's factory settings"
              onClick={() => void resetPreset()}
            >
              Reset to defaults
            </Btn>
          </>
        }
      >
        <div className={s.cardPad} id="ranked-targets-list" data-ui="">
          {!ready ? null : targets.length === 0 ? (
            <p className={s.listEmpty}>
              No targets yet. Add one below. With off-list quality not accepted, this would reject
              every download.
            </p>
          ) : (
            <OrderList
              label={`Quality priority for ${profileName || 'this profile'}`}
              items={targets.map((t) => ({
                id: targetId(t),
                name: rtLabel(t),
                mark: null,
              }))}
              role={(id, index) => {
                const t = byId.get(id);
                const kind = t && RT_LOSSLESS_FORMATS.includes(t.format) ? 'Lossless' : 'Lossy';
                if (index === 0) return `${kind} · most preferred`;
                if (untilCutoff && index > cutoff)
                  return `${kind} · kept until a better one turns up`;
                return kind;
              }}
              dropText={(_id, index) => `Drop here: ${ordinal(index + 1)}`}
              afterText={(order) =>
                `${byId.get(order[0]) ? rtLabel(byId.get(order[0])!) : ''} is first.`
              }
              marker={untilCutoff ? { after: cutoff, text: 'Upgrades stop here' } : null}
              onChange={(next) => reorderTargets(next)}
              removeLabel="Remove"
              minItems={0}
            />
          )}
        </div>
        <div className={s.rtAdd}>
          <span className={s.rtAddLabel}>Add a target</span>
          <Select
            id="rt-add-format"
            aria-label="Format to add"
            value={add.format}
            onChange={(event) => setAdd({ ...add, format: event.target.value })}
          >
            <optgroup label="Lossless">
              <option value="group:lossless">All lossless</option>
              <option value="flac">FLAC</option>
              <option value="alac">ALAC</option>
              <option value="wav">WAV / AIFF</option>
              <option value="dsf">DSD (DSF / DFF)</option>
            </optgroup>
            <optgroup label="Lossy">
              <option value="group:lossy">All lossy</option>
              <option value="mp3">MP3</option>
              <option value="aac">AAC</option>
              <option value="ogg">OGG (Vorbis)</option>
              <option value="opus">Opus</option>
              <option value="wma">WMA</option>
            </optgroup>
          </Select>
          <span className={s.rtAddFields} hidden={!lossless}>
            <Select
              id="rt-add-bitdepth"
              aria-label="Minimum bit depth"
              title="Minimum bit depth"
              value={add.bitDepth}
              onChange={(event) => setAdd({ ...add, bitDepth: event.target.value })}
              options={[
                { value: '', label: 'Any bit depth' },
                { value: '16', label: '16-bit' },
                { value: '24', label: '24-bit' },
              ]}
            />
            <Select
              id="rt-add-samplerate"
              aria-label="Minimum sample rate"
              title="Minimum sample rate"
              value={add.sampleRate}
              onChange={(event) => setAdd({ ...add, sampleRate: event.target.value })}
              options={[
                { value: '', label: 'Any sample rate' },
                { value: '44100', label: '≥ 44.1 kHz' },
                { value: '48000', label: '≥ 48 kHz' },
                { value: '96000', label: '≥ 96 kHz' },
                { value: '192000', label: '≥ 192 kHz' },
              ]}
            />
          </span>
          <span className={s.rtAddFields} hidden={lossless}>
            <Select
              id="rt-add-bitrate"
              aria-label="Minimum bitrate"
              title="Minimum bitrate"
              value={add.bitrate}
              onChange={(event) => setAdd({ ...add, bitrate: event.target.value })}
              options={[
                { value: '', label: 'Any bitrate' },
                { value: '96', label: '≥ 96 kbps' },
                { value: '128', label: '≥ 128 kbps' },
                { value: '192', label: '≥ 192 kbps' },
                { value: '256', label: '≥ 256 kbps' },
                { value: '320', label: '≥ 320 kbps' },
                { value: 'custom', label: 'Custom…' },
              ]}
            />
            <span id="rt-add-bitrate-custom-wrap" hidden={add.bitrate !== 'custom'}>
              <NumberField
                id="rt-add-bitrate-custom"
                aria-label="Minimum bitrate"
                min="0"
                max="5000"
                step="1"
                unit="kbps"
                value={add.custom}
                onChange={(event) => setAdd({ ...add, custom: event.target.value })}
                autoComplete="off"
                data-bwignore=""
                data-1p-ignore=""
                data-lpignore="true"
              />
            </span>
          </span>
          <Btn
            small
            icon="plus"
            onClick={() => editLadder((l) => withTargets(l, addTargets(l.targets, add)))}
          >
            Add target
          </Btn>
        </div>
        <Row
          label="Accept off-list quality when nothing in the list is available"
          htmlFor="quality-fallback-enabled"
          help="Off means a track with none of the above is not downloaded."
          more={
            <>
              This does <strong>not</strong> mean "walk down my list": the list already does that.
              When <strong>on</strong>, a file that matches <em>none</em> of your targets (a 16-bit
              FLAC, or an MP3 when you only listed 24-bit) is <strong>accepted anyway</strong> as a
              last resort. Turn it <strong>off</strong> to enforce the list: anything off-list is
              quarantined instead of completed.
            </>
          }
        >
          <Switch
            id="quality-fallback-enabled"
            checked={ladder.fallback}
            onChange={(event) => {
              const checked = event.target.checked;
              editLadder((l) => ({ ...l, fallback: checked }));
            }}
          />
        </Row>
        <More label="How the list is used" names="matching, and what is kept">
          <div className={s.cardPad}>
            <p className={s.fieldNote}>
              <strong>How it works:</strong> each download source is checked against this list top
              down. The first target a source can satisfy wins; a source that meets no target is
              skipped for the next one (source priority still decides between sources that can). For
              lossless, bit depth and sample rate decide the match. For MP3 and AAC the bitrate is a{' '}
              <em>minimum</em> (≥), so VBR and mono files are not falsely rejected. After download
              the real file is checked against the same list.
            </p>
            <p className={s.fieldNote}>
              <strong>Acquisition and what is kept:</strong> downsampling and the lossy copy run
              only after the downloaded file passes this list. SoulSync remembers the acquired
              quality, so keeping 16/44.1 or a lossy file only does not make the same track turn up
              as an upgrade again.
            </p>
          </div>
        </More>
      </Card>

      <SectionLabel title="Upgrades and searching" />
      <Card>
        <Row
          label="Upgrade until"
          htmlFor="quality-upgrade-policy"
          help="When a better copy of something you own turns up."
          more={
            <>
              <strong>No upgrades</strong> never replaces a file that already exists.{' '}
              <strong>Any accepted target</strong> stops upgrade findings as soon as a file matches
              any target in the list. <strong>Selected target or better</strong> keeps the Quality
              Upgrade job proposing replacements until the file reaches the selected place.
            </>
          }
        >
          <Segmented
            id="quality-upgrade-policy"
            label="Upgrade until"
            value={ladder.upgradePolicy}
            options={[
              { value: 'none', label: 'No upgrades' },
              { value: 'acceptable', label: 'Any accepted target' },
              { value: 'until_cutoff', label: 'Selected target or better' },
            ]}
            onChange={(value) =>
              editLadder((l) => ({ ...l, upgradePolicy: value as Ladder['upgradePolicy'] }))
            }
          />
        </Row>
        <Row
          id="quality-upgrade-cutoff-group"
          sub
          field
          hidden={!untilCutoff}
          label="Cutoff target"
          htmlFor="quality-upgrade-cutoff"
          help="The line in the list above shows where this is."
        >
          <Select
            id="quality-upgrade-cutoff"
            value={String(cutoff)}
            onChange={(event) =>
              editLadder((l) => ({
                ...l,
                cutoff: parseInt(event.target.value || '0', 10) || 0,
              }))
            }
          >
            {!ready ? null : targets.length ? (
              targets.map((t, i) => (
                <option key={i} value={String(i)}>
                  {`${i + 1}. ${rtLabel(t)}`}
                </option>
              ))
            ) : (
              <option value="0">Top ranked target</option>
            )}
          </Select>
        </Row>
        <Row
          label="Search strategy"
          htmlFor="quality-search-mode"
          help="Source priority uses the first source that has it. Best quality checks every source and takes the best."
          more={
            <>
              <strong>Source priority</strong> goes through your sources in order and downloads from
              the <em>first one</em> that has the track: fastest, fewest lookups.{' '}
              <strong>Best quality</strong> asks <em>every</em> source, then downloads the best copy
              it found, even if a faster source also had a worse one. Slower and more API calls, but
              you never settle for a worse file when a better one exists. Either way, the per-source
              retry limits stay the same.
            </>
          }
        >
          <Segmented
            id="quality-search-mode"
            label="Search strategy"
            value={ladder.searchMode}
            options={[
              { value: 'priority', label: 'Source priority' },
              { value: 'best_quality', label: 'Best quality' },
            ]}
            onChange={(value) =>
              editLadder((l) => ({
                ...l,
                searchMode: value === 'best_quality' ? 'best_quality' : 'priority',
              }))
            }
          />
        </Row>
        <Row
          id="quality-rank-candidates-group"
          sub
          hidden={ladder.searchMode === 'best_quality'}
          label="Rank-based download order"
          htmlFor="quality-rank-candidates"
          help="Downloads the best copy a source offers first, instead of the most likely-correct one."
          more={
            <>
              A single source often returns several copies of the same track. <strong>Off</strong>{' '}
              (the default): the most likely-correct, fastest copy is downloaded first.{' '}
              <strong>On</strong>: the best copy by your list is downloaded first; correctness and
              speed only break ties. Nothing is skipped and the source order does not change, so a
              track cannot go missing. (This is always how Best quality works, which is why it only
              shows for Source priority.)
            </>
          }
        >
          <Switch
            id="quality-rank-candidates"
            checked={ladder.rankCandidates}
            onChange={(event) => {
              const checked = event.target.checked;
              editLadder((l) => ({ ...l, rankCandidates: checked }));
            }}
          />
        </Row>
      </Card>

      <SectionLabel title="After a download" hint="What SoulSync keeps" />
      <Card>
        <Row
          label="Downsample Hi-Res FLAC to CD quality (16-bit / 44.1kHz)"
          htmlFor="downsample-hires"
          help="A 24-bit or high sample-rate FLAC is converted, and the hi-res original replaced."
          more="Its acquired quality is remembered for upgrade decisions, which prevents a download and downsample loop. Needs ffmpeg."
        >
          <Switch
            id="downsample-hires"
            checked={extras.downsample}
            onChange={(event) => editExtras({ downsample: event.target.checked })}
          />
        </Row>
        <Row
          label="Create lossy copy of downloaded FLAC files"
          htmlFor="lossy-copy-enabled"
          help="A second, smaller file beside each FLAC."
          more="After a lossless download, a lossy copy at the codec and bitrate below is made in the same folder. While the source is kept, it stays the canonical quality and the copy is a companion version. Needs ffmpeg."
        >
          <Switch
            id="lossy-copy-enabled"
            checked={extras.lossyEnabled}
            onChange={(event) => editExtras({ lossyEnabled: event.target.checked })}
          />
        </Row>
        <div id="lossy-copy-options" hidden={!extras.lossyEnabled}>
          <Row sub label="Codec" htmlFor="lossy-copy-codec">
            <Segmented
              id="lossy-copy-codec"
              label="Codec"
              value={extras.lossyCodec}
              options={[
                { value: 'mp3', label: 'MP3' },
                { value: 'opus', label: 'Opus' },
                { value: 'aac', label: 'AAC (M4A)' },
              ]}
              onChange={(value) =>
                editExtras({
                  lossyCodec: value,
                  lossyBitrate: lossyBitrateFor(value, extras.lossyBitrate),
                })
              }
            />
          </Row>
          <Row
            sub
            label="Bitrate"
            htmlFor="lossy-copy-bitrate"
            help={extras.lossyCodec === 'opus' ? 'Opus tops out at 256 kbps.' : undefined}
          >
            <Segmented
              id="lossy-copy-bitrate"
              label="Bitrate"
              value={extras.lossyBitrate}
              options={[
                { value: '320', label: '320 kbps', disabled: extras.lossyCodec === 'opus' },
                { value: '256', label: '256 kbps' },
                { value: '192', label: '192 kbps' },
                { value: '128', label: '128 kbps' },
              ]}
              onChange={(value) => editExtras({ lossyBitrate: value })}
            />
          </Row>
          <Row
            sub
            label={
              <>
                Keep lossy output only <Tag danger>deletes the lossless source</Tag>
              </>
            }
            htmlFor="lossy-copy-delete-original"
            help="After conversion the FLAC is removed for good, once the copy is checked."
            more="Its acquired quality stays recorded, so keeping only the lossy file is not shown as a new upgrade."
          >
            <Switch
              id="lossy-copy-delete-original"
              checked={extras.lossyDeleteOriginal}
              onChange={(event) => editExtras({ lossyDeleteOriginal: event.target.checked })}
            />
          </Row>
        </div>
        <p className={s.cardFoot}>
          <strong>One profile, two decisions:</strong> the quality list says what SoulSync acquires;
          these say what it keeps. A kept lossless and lossy pair is one track with two file
          versions, not two duplicate tracks.
        </p>
        <More
          label="Checks and imports"
          count={5}
          names="AcoustID-verified only, ffmpeg check, FLAC decode, replace on import, album releases"
        >
          <Row
            id="acoustid-require-verified-group"
            label="Only allow AcoustID-verified tracks for imports and downloads"
            htmlFor="acoustid-require-verified"
            help="A track AcoustID cannot confirm is quarantined instead of let in."
            more={
              <>
                Applies to both <strong>imports</strong> and <strong>downloads</strong>. When on, a
                track AcoustID runs but <em>cannot confirm</em> (no fingerprint match, metadata in
                another script: the "unverified" case) is quarantined. Downloads then try the next
                candidate; imports just quarantine. Only a clean AcoustID pass is kept.{' '}
                <strong>Use with care.</strong> "Could not confirm" is common for perfectly good
                tracks: anything not in its fingerprint database, such as new or obscure releases,
                classical, remixes, live cuts, remasters and non-Latin titles. With this on, all of
                those are quarantined, so expect to approve many by hand. Nothing is lost
                (quarantined files can be approved). Leave it <strong>off</strong> to keep the safer
                default: unconfirmed tracks import with the "unverified" badge. Lookup errors (a
                rate limit, an outage) always import, so an AcoustID outage never stalls you. Needs
                AcoustID switched on.
              </>
            }
          >
            <Switch
              id="acoustid-require-verified"
              checked={extras.acoustidRequired}
              onChange={(event) => editExtras({ acoustidRequired: event.target.checked })}
            />
          </Row>
          <Row
            label="Verify real audio with ffmpeg"
            htmlFor="audio-completeness-check"
            help="Decodes every download to catch padded 30-second previews and mostly silent files."
            more={
              <>
                These are files whose header lies about the length, so the usual checks cannot catch
                them. <strong>Off by default:</strong> it fully decodes each file, the most
                CPU-heavy step after a download. Most sources (HiFi, Qobuz) already catch previews,
                so only turn this on if padded or silent files slip through. Applies to every
                download source.
              </>
            }
          >
            <Switch
              id="audio-completeness-check"
              checked={extras.deepVerify}
              onChange={(event) => editExtras({ deepVerify: event.target.checked })}
            />
          </Row>
          <Row
            label={
              <>
                Verify FLAC decode with flac -t <Tag>applies to every profile</Tag>
              </>
            }
            htmlFor="verify-flac-decode"
            help="A second integrity check: a FLAC is fully decoded and its MD5 checked."
            more={
              <>
                A FLAC that passes the size, parse and duration checks is fully decoded with{' '}
                <code>flac -t</code>, which also checks the STREAMINFO MD5. A file that fails is
                quarantined and the next candidate is tried, so a damaged replacement never takes
                the place of a good file. <strong>Off by default.</strong> It fails open: if the
                flac binary is missing or the check times out, the file is left alone.
              </>
            }
          >
            <Switch
              id="verify-flac-decode"
              checked={state.settings.post_processing?.verify_flac_decode === true}
              onChange={(event) =>
                store.set('post_processing', 'verify_flac_decode', event.target.checked)
              }
            />
          </Row>
          <Row
            label="Replace lower quality files on import"
            htmlFor="import-replace-lower-quality"
            help="An import of a track you have at a lower quality replaces it."
            more={
              <>
                When importing from Staging, a track already in your library at a lower quality (an
                MP3) is replaced with the better version (a FLAC). Off: the library copy is kept and
                the import file skipped. Imported files are always checked against this profile's
                list; there is no separate switch for that, since an empty list (or off-list quality
                accepted) already means "accept anything".
              </>
            }
          >
            <Switch
              id="import-replace-lower-quality"
              checked={extras.replaceLower}
              onChange={(event) => editExtras({ replaceLower: event.target.checked })}
            />
          </Row>
          <Row
            label="Import from album releases"
            htmlFor="quality-release-import-mode"
            field
            help="When a Usenet or torrent download of a requested track is a whole album."
            more={
              <>
                <strong>Requested tracks only</strong> imports just the track you asked for.{' '}
                <strong>Also the album's other tracks</strong> then imports every other track of
                that album found in the release and not in your library yet. Each goes through the
                normal import pipeline with this quality profile: a track that fails a check is
                quarantined on its own, and the ones that pass are imported.
              </>
            }
          >
            <Select
              id="quality-release-import-mode"
              value={ladder.releaseImportMode}
              onChange={(event) => {
                const value = event.target.value;
                editLadder((l) => ({
                  ...l,
                  releaseImportMode: value === 'album_tracks' ? 'album_tracks' : 'requested_tracks',
                }));
              }}
              options={[
                { value: 'requested_tracks', label: 'Requested tracks only (default)' },
                { value: 'album_tracks', label: "Also the album's other tracks that pass" },
              ]}
            />
          </Row>
        </More>
      </Card>
    </div>
  );
}

const ORDINALS = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth'];
const ordinal = (n: number) => ORDINALS[n - 1] ?? `number ${n}`;
