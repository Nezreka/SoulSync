/**
 * The Quality tab of Settings (music), ported from index.html and settings.js
 * (SETTINGS_PORT.md, step 6). Same markup, classes and ids: the Music Quality
 * tile with the ranked target list, Quality on Import, Album Releases,
 * Post-Download Conversion and Audio Verification on the left, the Quality
 * Profiles panel on the right, and the Manage overview it opens.
 *
 * Every control but "Verify FLAC decode" belongs to the profile on screen.
 * An edit is saved to that profile (POST /api/quality-profile/custom/<id>/update)
 * through the settings store, so autosave, the autosave switch and the save
 * button treat it like any other change. The server copies the default
 * profile into config on its own.
 */

import {
  type DragEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';

import type { SettingsTabProps } from '../host';

import { HelpIcon, Section, helpBits, useHelp } from './parts';
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
  rtLabel,
  rtSelectionIsLossless,
  withTargets,
} from './quality';

const TAB = 'quality';

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

function Badge() {
  return (
    <span
      className="qp-governed-badge"
      title="Saved under the active profile in the panel on the right"
    >
      Profile
    </span>
  );
}

/** a setting-row: the control and its "i", the help under it, then the line */
function Row({
  id,
  control,
  help,
  bodyClass = 'setting-help-body',
}: {
  id?: string;
  control: ReactNode;
  help: ReactNode;
  bodyClass?: string;
}) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group" id={id}>
      <div className="setting-row">
        {control}
        {bits.icon}
      </div>
      <div className={bodyClass} hidden={!state.open}>
        {help}
      </div>
      {bits.line}
    </div>
  );
}

/** a select under its own label row, with the help after it */
function SelectRow({
  id,
  label,
  extra,
  help,
  children,
}: {
  id: string;
  label: string;
  extra?: ReactNode;
  help: ReactNode;
  children: ReactNode;
}) {
  const state = useHelp();
  const bits = helpBits(help, state);
  return (
    <div className="form-group">
      <div className="setting-row" style={{ marginBottom: '4px' }}>
        <label htmlFor={id} style={{ fontWeight: 600 }}>
          {label}
        </label>
        {bits.icon}
      </div>
      {children}
      {extra}
      <div className="help-text setting-help-body" hidden={!state.open}>
        {help}
      </div>
      {bits.line}
    </div>
  );
}

function Tick({
  id,
  label,
  checked,
  onChange,
  style,
}: {
  id: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  style?: Record<string, string>;
}) {
  return (
    <label className="checkbox-label" style={style}>
      <input
        type="checkbox"
        id={id}
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      {label}
    </label>
  );
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
      className="qp-profile-name-input"
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
    <div className="qp-profile-new-row">
      <input
        ref={ref}
        type="text"
        className="qp-profile-new-input"
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

function ManagerHelp() {
  const help = useHelp();
  return (
    <>
      <div className="qp-manager-info-row setting-row">
        <span className="qp-manager-info-label">What does this manage?</span>
        <HelpIcon {...help} />
      </div>
      <div className="help-text setting-help-body qp-manager-info" hidden={!help.open}>
        Two things read a profile today: normal downloads/Wishlist items always follow the{' '}
        <strong>Default</strong> below, and Auto-Import can optionally use a different one instead
        (falls back to Default when unset). A profile's "Upgrade until" setting controls exactly how
        far below it counts as needing an upgrade — the Quality Upgrade Finder then actively
        searches for and proposes a replacement, while Quality Check only flags (you choose
        re-download/delete/ignore per finding); both read the cutoff live, same as the download
        pipeline. Per-artist/ album/track assignment is planned for the Library Manager; it will
        plug into this exact mechanism.
      </div>
    </>
  );
}

export function QualityTab({ store }: SettingsTabProps) {
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
  const [dragging, setDragging] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const [renaming, setRenaming] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [manager, setManager] = useState<'never' | 'open' | 'closed'>('never');

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

  async function openManager() {
    await loadRows();
    setManager('open');
  }

  // ── the ranked list ──

  const targets = ladder.targets;
  const moveTarget = (i: number, dir: number) => {
    const j = i + dir;
    if (j < 0 || j >= targets.length) return;
    const next = [...targets];
    [next[i], next[j]] = [next[j], next[i]];
    editLadder((l) => withTargets(l, next));
  };
  const reorderTarget = (from: number, to: number) => {
    const next = [...targets];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    editLadder((l) => withTargets(l, next));
  };
  const deleteTarget = (i: number) =>
    editLadder((l) =>
      withTargets(
        l,
        l.targets.filter((_, k) => k !== i),
      ),
    );

  const targetRow = (t: RankedTarget, i: number) => (
    <div
      key={`${i}-${rtLabel(t)}`}
      className={[
        'ranked-target-row',
        dragging === i ? 'rt-dragging' : '',
        dragOver === i ? 'rt-dragover' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      draggable
      data-index={String(i)}
      onDragStart={(event: DragEvent) => {
        event.dataTransfer?.setData('text/plain', String(i));
        setDragging(i);
      }}
      onDragEnd={() => setDragging(null)}
      onDragOver={(event) => {
        event.preventDefault();
        setDragOver(i);
      }}
      onDragLeave={() => setDragOver(null)}
      onDrop={(event) => {
        event.preventDefault();
        setDragOver(null);
        const from = parseInt(event.dataTransfer?.getData('text/plain') ?? '', 10);
        if (!Number.isNaN(from) && from !== i) reorderTarget(from, i);
      }}
    >
      <span className="rt-handle" title="Drag to reorder">
        ⠿
      </span>
      <span className="rt-rank">{i + 1}</span>
      <span className="rt-label">{rtLabel(t)}</span>
      <span className="rt-spacer" />
      <button type="button" className="rt-move" title="Move up" onClick={() => moveTarget(i, -1)}>
        ▲
      </button>
      <button type="button" className="rt-move" title="Move down" onClick={() => moveTarget(i, 1)}>
        ▼
      </button>
      <button type="button" className="rt-del" title="Remove" onClick={() => deleteTarget(i)}>
        🗑
      </button>
    </div>
  );

  const lossless = rtSelectionIsLossless(add.format);
  const rankedHelp = useHelp();
  const cutoff = clampCutoff(ladder.cutoff, targets);

  // ── the side panel ──

  const editingRow =
    editingId === null ? undefined : rows.find((p) => p.id === editingId && !p.is_default);

  const profileRow = (profile: ProfileRow) => {
    const isEditing = profile.id === editingId && !profile.is_default;
    const isAutoImport = profile.id === autoImportId;
    return (
      <div
        key={profile.id}
        className={
          'qp-profile-row' + (profile.is_default ? ' active' : '') + (isEditing ? ' editing' : '')
        }
      >
        <span
          className="qp-profile-dot"
          title={
            profile.is_default
              ? 'Currently the active default'
              : `Set '${profile.name}' as the active default (applies immediately, incl. anything downloading right now)`
          }
          onClick={() => void setDefault(profile.id, profile.name)}
        />
        <span
          className="qp-profile-text"
          title={
            profile.is_default
              ? "View this profile's settings (it's already the active default)"
              : `View/edit '${profile.name}'s settings — does not activate it`
          }
          onClick={() => void previewProfile(profile.id)}
        >
          {renaming === profile.id ? (
            <RenameInput
              name={profile.name}
              onCommit={(value) => void rename(profile, value)}
              onCancel={() => {
                setRenaming(null);
                void loadRows();
              }}
            />
          ) : (
            <span className="qp-profile-name">{profile.name}</span>
          )}
          <span className="qp-profile-sub">{profileSummary(profile)}</span>
        </span>
        {profile.is_default || isAutoImport ? (
          <span className="qp-profile-tags">
            {profile.is_default ? (
              <span
                className="qp-profile-tag qp-tag-default"
                title="Used by normal downloads and Wishlist items right now"
              >
                Downloads
              </span>
            ) : null}
            {isAutoImport ? (
              <span
                className="qp-profile-tag qp-tag-autoimport"
                title="Also used by Auto-Import (Settings → Import)"
              >
                Auto-Import
              </span>
            ) : null}
          </span>
        ) : null}
        <span className="qp-profile-actions">
          <button
            type="button"
            className={'qp-profile-action qp-action-autoimport' + (isAutoImport ? ' active' : '')}
            title={
              isAutoImport
                ? `Stop using '${profile.name}' for Auto-Import (revert to the default profile)`
                : `Use '${profile.name}' for Auto-Import`
            }
            onClick={(event) => {
              event.stopPropagation();
              void toggleAutoImport(profile.id);
            }}
          >
            ⇩
          </button>
          <button
            type="button"
            className="qp-profile-action qp-action-rename"
            title={`Rename '${profile.name}'`}
            onClick={(event) => {
              event.stopPropagation();
              setRenaming(profile.id);
            }}
          >
            ✏
          </button>
          <button
            type="button"
            className="qp-profile-action qp-action-update"
            title={`Save the current Quality-page settings to '${profile.name}' now`}
            onClick={(event) => {
              event.stopPropagation();
              void saveNow(profile.id, profile.name);
            }}
          >
            ✎
          </button>
          {rows.length > 1 ? (
            <button
              type="button"
              className="qp-profile-action qp-action-delete"
              title={`Delete '${profile.name}'`}
              onClick={(event) => {
                event.stopPropagation();
                void remove(profile.id, profile.name);
              }}
            >
              ×
            </button>
          ) : null}
        </span>
      </div>
    );
  };

  const defaultRow = rows.find((p) => p.is_default);
  const defaultName = defaultRow ? defaultRow.name : '—';
  const autoImportName = autoImportId
    ? rows.find((p) => p.id === autoImportId)?.name || 'Unknown profile'
    : `Uses Default (${defaultName})`;

  const managerModal =
    manager === 'never'
      ? null
      : createPortal(
          <div
            id="qp-manager-overlay"
            className={manager === 'open' ? 'modal-overlay' : 'modal-overlay hidden'}
            onClick={(event) => {
              if (event.target === event.currentTarget) setManager('closed');
            }}
          >
            <div
              className="enhanced-bulk-modal qp-manager-modal"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="enhanced-bulk-modal-header">
                <h3>Quality Profiles — Manage</h3>
                <button className="enhanced-bulk-modal-close" onClick={() => setManager('closed')}>
                  &times;
                </button>
              </div>
              <div className="enhanced-bulk-modal-body qp-manager-body">
                <ManagerHelp />
                <div id="qp-manager-rows" className="qp-manager-rows">
                  {rows.length ? (
                    <>
                      <div className="qp-manager-section-title">Active now</div>
                      <div className="qp-manager-status-row">
                        <span className="qp-manager-status-label">Downloads &amp; Wishlist</span>
                        <span className="qp-manager-status-value">{defaultName}</span>
                      </div>
                      <div className="qp-manager-status-row">
                        <span className="qp-manager-status-label">Auto-Import</span>
                        <span className="qp-manager-status-value">{autoImportName}</span>
                        {autoImportId ? (
                          <button
                            type="button"
                            className="qp-manager-action clear"
                            onClick={() => void toggleAutoImport(autoImportId)}
                          >
                            Use default
                          </button>
                        ) : null}
                      </div>
                      <div className="qp-manager-section-title">Profiles</div>
                      {rows.map((profile) => (
                        <div className="qp-manager-row" key={profile.id}>
                          <div className="qp-manager-row-main">
                            <span className="qp-manager-row-name">{profile.name}</span>
                            <span className="qp-manager-row-sub">{profileSummary(profile)}</span>
                          </div>
                          <button
                            type="button"
                            className={`qp-manager-action ${profile.is_default ? 'active' : ''}`}
                            onClick={() => void setDefault(profile.id, profile.name)}
                          >
                            {profile.is_default ? 'Default' : 'Set default'}
                          </button>
                          <button
                            type="button"
                            className={`qp-manager-action ${autoImportId === profile.id ? 'active' : ''}`}
                            onClick={() => void toggleAutoImport(profile.id)}
                          >
                            {autoImportId === profile.id ? 'Auto-Import' : 'Use for import'}
                          </button>
                        </div>
                      ))}
                    </>
                  ) : (
                    <div className="qp-profile-list-empty">No profiles yet.</div>
                  )}
                </div>
              </div>
            </div>
          </div>,
          document.body,
        );

  const presetButton = (name: string, title: string, label: string) => (
    <button
      className={ladder.preset === name ? 'preset-button active' : 'preset-button'}
      onClick={() => void applyPreset(name)}
      title={title}
    >
      {label}
    </button>
  );

  return (
    <div className="qp-layout" data-stg={TAB}>
      <div className="qp-main" data-stg={TAB}>
        <div id="quality-profile-tile" data-stg={TAB}>
          <div
            className="settings-section-header collapsed"
            onClick={(event) => {
              const header = event.currentTarget;
              header.classList.toggle('collapsed');
              const body = header.nextElementSibling as HTMLElement | null;
              if (!body) return;
              body.classList.toggle('collapsed');
              body.style.display = body.classList.contains('collapsed') ? 'none' : '';
            }}
            role="button"
            tabIndex={0}
            aria-expanded="false"
          >
            <span className="settings-section-arrow">&#9660;</span>
            <h3>Music Quality</h3>
            <Badge />
            <span className="settings-section-hint">Format priorities, bitrate, bit depth</span>
          </div>
          <div
            className="settings-section-body collapsed"
            data-stg={TAB}
            style={{ display: 'none' }}
          >
            <div className="settings-group" id="quality-profile-section" data-stg={TAB}>
              <div className="quality-presets">
                <label>Quick Presets:</label>
                <div className="preset-buttons">
                  {presetButton(
                    'audiophile',
                    'FLAC only, strict size constraints',
                    '🎧 Audiophile',
                  )}
                  {presetButton('balanced', 'FLAC preferred, MP3 fallback', '⚖️ Balanced')}
                  {presetButton('space_saver', 'MP3 preferred, smaller sizes', '💾 Space Saver')}
                </div>
                <div className="quality-presets-footer">
                  <span
                    className="help-text"
                    style={{
                      padding: '0',
                      border: '0',
                      background: 'none',
                      margin: '0',
                      flex: '1',
                    }}
                  >
                    Edits below are saved <em>per preset</em> — switch away and back and your
                    changes are still there.
                  </span>
                  <button
                    type="button"
                    className="preset-reset-btn"
                    onClick={() => void resetPreset()}
                    title="Restore the selected preset's factory settings"
                  >
                    <span className="preset-reset-icon">↺</span> Reset to defaults
                  </button>
                </div>
              </div>

              <div className="ranked-targets-editor">
                <div className="setting-row">
                  <label className="ranked-targets-label" style={{ margin: '0' }}>
                    Quality priority (drag to reorder — 1st = most preferred):
                  </label>
                  <HelpIcon {...rankedHelp} />
                </div>
                <div className="help-text setting-help-body" hidden={!rankedHelp.open}>
                  <strong>How it works:</strong> Each download source is checked against this list
                  top-down. The first target a source can satisfy wins; a source that meets no
                  target is skipped for the next one (source priority still decides between sources
                  that can). For lossless, bit depth + sample rate decide the match. For MP3/AAC the
                  bitrate is a <em>minimum</em> threshold (≥), so VBR and mono files aren't falsely
                  rejected. After download the real file is verified against the same list. With
                  fallback off, a track is left missing rather than accepting a quality below every
                  target.
                  <br />
                  <br />
                  <strong>Acquisition vs retained output:</strong> Downsample and Lossy Copy run
                  only after the downloaded file passes this gate. SoulSync remembers the acquired
                  quality, so intentionally retaining 16/44.1 or a lossy-only output does not make
                  the same track appear as an upgrade again.
                </div>
                <div id="ranked-targets-list" className="ranked-targets-list">
                  {!ready ? null : targets.length === 0 ? (
                    <div className="ranked-targets-empty">
                      No targets yet — add one below. With fallback off this would reject every
                      download.
                    </div>
                  ) : (
                    targets.map(targetRow)
                  )}
                </div>

                <div className="ranked-target-add">
                  <select
                    id="rt-add-format"
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
                  </select>
                  <span className="rt-lossless-fields" style={{ display: lossless ? '' : 'none' }}>
                    <select
                      id="rt-add-bitdepth"
                      title="Minimum bit depth"
                      value={add.bitDepth}
                      onChange={(event) => setAdd({ ...add, bitDepth: event.target.value })}
                    >
                      <option value="">Any bit depth</option>
                      <option value="16">16-bit</option>
                      <option value="24">24-bit</option>
                    </select>
                    <select
                      id="rt-add-samplerate"
                      title="Minimum sample rate"
                      value={add.sampleRate}
                      onChange={(event) => setAdd({ ...add, sampleRate: event.target.value })}
                    >
                      <option value="">Any sample rate</option>
                      <option value="44100">≥ 44.1 kHz</option>
                      <option value="48000">≥ 48 kHz</option>
                      <option value="96000">≥ 96 kHz</option>
                      <option value="192000">≥ 192 kHz</option>
                    </select>
                  </span>
                  <span className="rt-lossy-fields" style={{ display: lossless ? 'none' : '' }}>
                    <select
                      id="rt-add-bitrate"
                      title="Minimum bitrate"
                      value={add.bitrate}
                      onChange={(event) => setAdd({ ...add, bitrate: event.target.value })}
                    >
                      <option value="">Any bitrate</option>
                      <option value="96">≥ 96 kbps</option>
                      <option value="128">≥ 128 kbps</option>
                      <option value="192">≥ 192 kbps</option>
                      <option value="256">≥ 256 kbps</option>
                      <option value="320">≥ 320 kbps</option>
                      <option value="custom">Custom…</option>
                    </select>
                    <label
                      className="rt-inline-label"
                      id="rt-add-bitrate-custom-wrap"
                      style={{ display: add.bitrate === 'custom' ? '' : 'none' }}
                    >
                      ≥{' '}
                      <input
                        type="number"
                        id="rt-add-bitrate-custom"
                        min="0"
                        max="5000"
                        step="1"
                        value={add.custom}
                        onChange={(event) => setAdd({ ...add, custom: event.target.value })}
                        style={{ width: '72px' }}
                        autoComplete="off"
                        data-bwignore=""
                        data-1p-ignore=""
                        data-lpignore="true"
                      />{' '}
                      kbps
                    </label>
                  </span>
                  <button
                    type="button"
                    className="rt-add-btn"
                    onClick={() => editLadder((l) => withTargets(l, addTargets(l.targets, add)))}
                  >
                    + Add target
                  </button>
                </div>
              </div>

              <SelectRow
                id="quality-upgrade-policy"
                label="Upgrade until"
                extra={
                  <div
                    id="quality-upgrade-cutoff-group"
                    className="quality-upgrade-cutoff-group"
                    style={{ display: ladder.upgradePolicy === 'until_cutoff' ? '' : 'none' }}
                  >
                    <label htmlFor="quality-upgrade-cutoff">Cutoff target</label>
                    <select
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
                    </select>
                  </div>
                }
                help={
                  <>
                    <strong>No upgrades</strong> never replaces a file that already exists.
                    <br />
                    <br />
                    <strong>Any accepted target</strong> stops upgrade findings as soon as a file
                    matches any target in the list.
                    <br />
                    <br />
                    <strong>Selected target or better</strong> keeps the Quality Upgrade job
                    proposing replacements until the file reaches the selected rank.
                  </>
                }
              >
                <select
                  id="quality-upgrade-policy"
                  style={{ width: '100%', maxWidth: '420px' }}
                  value={ladder.upgradePolicy}
                  onChange={(event) =>
                    editLadder((l) => ({
                      ...l,
                      upgradePolicy: event.target.value as Ladder['upgradePolicy'],
                    }))
                  }
                >
                  <option value="none">No upgrades</option>
                  <option value="acceptable">Any accepted target</option>
                  <option value="until_cutoff">Selected target or better</option>
                </select>
              </SelectRow>

              <SelectRow
                id="quality-search-mode"
                label="Search strategy"
                help={
                  <>
                    <strong>Source priority</strong> goes through your sources in order and
                    downloads from the <em>first one</em> that has the track. Fastest, fewest
                    lookups.
                    <br />
                    <br />
                    <strong>Best quality</strong> asks <em>every</em> source, then downloads the
                    highest-quality copy it found — even if a faster source also had a lower-quality
                    one. Slower and more API calls, but you never settle for a worse file when a
                    better one exists.
                    <br />
                    <br />
                    Either way, the per-source retry limits stay the same.
                  </>
                }
              >
                <select
                  id="quality-search-mode"
                  style={{ width: '100%', maxWidth: '420px' }}
                  value={ladder.searchMode}
                  onChange={(event) =>
                    editLadder((l) => ({
                      ...l,
                      searchMode:
                        event.target.value === 'best_quality' ? 'best_quality' : 'priority',
                    }))
                  }
                >
                  <option value="priority">
                    Source priority — fast (use the first source that has it)
                  </option>
                  <option value="best_quality">
                    Best quality — thorough (check every source, take the best)
                  </option>
                </select>
              </SelectRow>

              <RankCandidates
                hidden={ladder.searchMode === 'best_quality'}
                checked={ladder.rankCandidates}
                onChange={(checked) => editLadder((l) => ({ ...l, rankCandidates: checked }))}
              />

              <Row
                bodyClass="help-text setting-help-body"
                control={
                  <Tick
                    id="quality-fallback-enabled"
                    label="Accept off-list quality when nothing in the list is available"
                    style={{ margin: '0' }}
                    checked={ladder.fallback}
                    onChange={(checked) => editLadder((l) => ({ ...l, fallback: checked }))}
                  />
                }
                help={
                  <>
                    This does <strong>not</strong> mean "walk down my list" — the list already does
                    that on its own.
                    <br />
                    <br />
                    When <strong>on</strong>, a file that matches <em>none</em> of your targets
                    (e.g. a 16-bit FLAC or an MP3 when you only listed 24-bit) is{' '}
                    <strong>accepted anyway</strong> as a last resort.
                    <br />
                    <br />
                    Turn it <strong>off</strong> to strictly enforce your list — anything off-list
                    is then quarantined instead of completed.
                  </>
                }
              />
            </div>
          </div>
        </div>

        <Section
          tab={TAB}
          title="Quality on Import"
          badge={<Badge />}
          hint="Apply the profile to manually imported files"
        >
          <div className="settings-group" data-stg={TAB}>
            <Row
              control={
                <Tick
                  id="import-replace-lower-quality"
                  label="Replace lower quality files on import"
                  checked={extras.replaceLower}
                  onChange={(checked) => editExtras({ replaceLower: checked })}
                />
              }
              help={
                <>
                  When importing from Staging, if a track already exists in your library at a lower
                  quality (e.g. MP3), it will be replaced with the higher quality version (e.g.
                  FLAC). If disabled, existing tracks are always kept and the import file is
                  skipped.
                  <br />
                  <br />
                  <strong>Note:</strong> imported files are always checked against your Quality
                  Profile's priority list above — there's no separate on/off switch for that, since
                  an empty list (or Fallback turned on) already means "accept anything."
                </>
              }
            />
          </div>
        </Section>

        <Section
          tab={TAB}
          title="Album Releases"
          badge={<Badge />}
          hint="What to import when a Usenet or torrent download is a whole album"
        >
          <div className="settings-group" data-stg={TAB}>
            <SelectRow
              id="quality-release-import-mode"
              label="Import from album releases"
              help={
                <>
                  A requested track often arrives inside a whole album release.{' '}
                  <strong>Requested tracks only</strong> imports just the track you asked for.
                  <br />
                  <br />
                  <strong>Also the album's other tracks</strong> then imports every other track of
                  that album found in the release and not yet in your library. Each one goes through
                  the normal import pipeline with this quality profile, like a wishlist download: a
                  track that fails a check is quarantined on its own, and the tracks that pass are
                  imported.
                </>
              }
            >
              <select
                id="quality-release-import-mode"
                style={{ width: '100%', maxWidth: '420px' }}
                value={ladder.releaseImportMode}
                onChange={(event) =>
                  editLadder((l) => ({
                    ...l,
                    releaseImportMode:
                      event.target.value === 'album_tracks' ? 'album_tracks' : 'requested_tracks',
                  }))
                }
              >
                <option value="requested_tracks">Requested tracks only (default)</option>
                <option value="album_tracks">Also the album's other tracks that pass</option>
              </select>
            </SelectRow>
          </div>
        </Section>

        <Section
          tab={TAB}
          title="Post-Download Conversion"
          badge={<Badge />}
          hint="Downsample hi-res, make lossy copies"
        >
          <div className="settings-group" data-stg={TAB}>
            <Row
              control={
                <Tick
                  id="downsample-hires"
                  label="Downsample Hi-Res FLAC to CD quality (16-bit / 44.1kHz)"
                  checked={extras.downsample}
                  onChange={(checked) => editExtras({ downsample: checked })}
                />
              }
              help={
                <>
                  When a 24-bit or high sample-rate FLAC is downloaded, it is automatically
                  converted to 16-bit/44.1kHz. The hi-res original is <strong>replaced</strong>, not
                  kept alongside. Its acquired quality is remembered for upgrade decisions,
                  preventing a download/downsample loop. Requires ffmpeg.
                </>
              }
            />
            <Row
              control={
                <Tick
                  id="lossy-copy-enabled"
                  label="Create lossy copy of downloaded FLAC files"
                  checked={extras.lossyEnabled}
                  onChange={(checked) => editExtras({ lossyEnabled: checked })}
                />
              }
              help="After downloading a lossless file, a lossy derivative at the selected codec and bitrate is created in the same folder. When the source is kept, it remains the canonical quality and the derivative is treated as a companion version. Requires ffmpeg."
            />
            <div
              id="lossy-copy-options"
              style={{ display: extras.lossyEnabled ? 'block' : 'none' }}
            >
              <div className="form-group">
                <label>Codec:</label>
                <select
                  id="lossy-copy-codec"
                  value={extras.lossyCodec}
                  onChange={(event) =>
                    editExtras({
                      lossyCodec: event.target.value,
                      lossyBitrate: lossyBitrateFor(event.target.value, extras.lossyBitrate),
                    })
                  }
                >
                  <option value="mp3">MP3</option>
                  <option value="opus">Opus</option>
                  <option value="aac">AAC (M4A)</option>
                </select>
              </div>
              <div className="form-group">
                <label>Bitrate:</label>
                <select
                  id="lossy-copy-bitrate"
                  value={extras.lossyBitrate}
                  onChange={(event) => editExtras({ lossyBitrate: event.target.value })}
                >
                  {/* Opus tops out at 256 kbps */}
                  <option value="320" disabled={extras.lossyCodec === 'opus'}>
                    320 kbps
                  </option>
                  <option value="256">256 kbps</option>
                  <option value="192">192 kbps</option>
                  <option value="128">128 kbps</option>
                </select>
              </div>
              <div className="form-group" style={{ marginTop: '12px' }}>
                <Tick
                  id="lossy-copy-delete-original"
                  label="Keep lossy output only — delete the lossless source after conversion"
                  checked={extras.lossyDeleteOriginal}
                  onChange={(checked) => editExtras({ lossyDeleteOriginal: checked })}
                />
              </div>
              <div className="help-text" style={{ color: 'rgba(255, 100, 100, 0.8)' }}>
                Warning: The acquired lossless file will be permanently deleted after the output is
                verified. Its acquisition quality remains recorded, so this retention choice is not
                shown as a new upgrade.
              </div>
            </div>
            <div className="help-text">
              <strong>One profile, two decisions:</strong> the quality list controls what SoulSync
              acquires; these options control what it retains. A retained lossless + lossy pair is
              one track with two file versions, not two duplicate tracks.
            </div>
          </div>
        </Section>

        <Section
          tab={TAB}
          title="Audio Verification"
          badge={<Badge />}
          hint="AcoustID identity match + truncated/silent detection"
        >
          <div className="settings-group" data-stg={TAB}>
            <Row
              id="acoustid-require-verified-group"
              control={
                <Tick
                  id="acoustid-require-verified"
                  label="Only allow AcoustID-verified tracks for imports and downloads"
                  checked={extras.acoustidRequired}
                  onChange={(checked) => editExtras({ acoustidRequired: checked })}
                />
              }
              help={
                <>
                  Applies to both <strong>imports</strong> and <strong>downloads</strong>. When{' '}
                  <strong>on</strong>, a track that AcoustID runs but <em>cannot confirm</em> (no
                  fingerprint match / cross-script metadata — the ⚠ "unverified" case) is{' '}
                  <strong>quarantined</strong> instead of being allowed into the library. Downloads
                  then try the next candidate; imports just quarantine (no candidate to retry). Only
                  a clean AcoustID <strong>pass</strong> is kept.
                  <br />
                  <br />
                  ⚠️ <strong>Use with care.</strong> AcoustID "could not confirm" is <em>common</em>{' '}
                  for perfectly good tracks — anything not in its fingerprint database: new/obscure
                  releases, classical, remixes, live cuts, remasters, non-Latin-script titles. With
                  this on, <strong>all of those get quarantined</strong>, so you may end up with
                  many legitimate files to review and approve by hand. Nothing is lost (quarantined
                  files can be approved), but expect manual cleanup. Leave this <strong>off</strong>{' '}
                  to keep the safer default: unconfirmed tracks import with the ⚠ "unverified" badge
                  so you can review them without anything being blocked. Transient lookup errors
                  (rate-limit / outage) always import, so an AcoustID outage never stalls you.
                  Requires AcoustID enabled.
                </>
              }
            />
            <Row
              control={
                <Tick
                  id="audio-completeness-check"
                  label="Verify real audio with ffmpeg"
                  checked={extras.deepVerify}
                  onChange={(checked) => editExtras({ deepVerify: checked })}
                />
              }
              help={
                <>
                  Decodes every downloaded file with ffmpeg to catch fake 30s previews padded to
                  full length and mostly-silent files — the kind whose header lies about the length
                  so mutagen can't catch them. <strong>Off by default:</strong> this fully decodes
                  each file, the most CPU-heavy post-processing step. Most sources (HiFi, Qobuz)
                  already catch previews on their own, so only turn this on if you see padded/silent
                  files slip through. Applies to all download sources.
                </>
              }
            />
            <Row
              control={
                <Tick
                  id="verify-flac-decode"
                  label="Verify FLAC decode with flac -t"
                  checked={state.settings.post_processing?.verify_flac_decode === true}
                  onChange={(checked) =>
                    store.set('post_processing', 'verify_flac_decode', checked)
                  }
                />
              }
              help={
                <>
                  Opt-in second integrity tier: a FLAC that passes the size/parse/duration checks is
                  fully decoded with <code>flac -t</code>, which also verifies the STREAMINFO MD5. A
                  file that fails is quarantined and the next candidate is tried, so a damaged
                  replacement can never take the place of a good file.{' '}
                  <strong>Off by default.</strong> Fails open — if the flac binary is missing or the
                  check times out, the file is left alone rather than quarantined.
                </>
              }
            />
          </div>
        </Section>
      </div>

      <div className="qp-side-panel" id="qp-side-panel" data-stg={TAB}>
        <div className="qp-side-panel-header">
          <h3 className="qp-side-panel-title">Quality Profiles</h3>
          <button type="button" className="qp-manage-btn" onClick={() => void openManager()}>
            Manage
          </button>
        </div>
        <p className="qp-side-panel-hint">
          Click a profile's name to view/edit its settings on the left. Changes autosave to that
          profile, and Save Settings saves it too; viewing never activates it. Per row: ● set as
          active default, ✏ rename, ✎ save now, ⇩ use for Auto-Import, × delete.
        </p>
        <div
          className="qp-editing-banner"
          id="qp-editing-banner"
          style={{ display: editingRow ? '' : 'none' }}
        >
          Editing{' '}
          <strong className="qp-editing-banner-name">{editingRow?.name ?? 'this profile'}</strong> —
          not the active default. Changes autosave to this profile; the save status at the top saves
          it right away too.
        </div>
        <div className="qp-profile-list" id="qp-profile-list">
          {!rowsLoaded ? null : rows.length ? (
            rows.map(profileRow)
          ) : (
            <span className="qp-profile-list-empty">No profiles yet.</span>
          )}
        </div>
        <div className="qp-profile-new" id="qp-profile-new">
          {adding ? (
            <NewProfileInput
              onCommit={(name) => void saveAsNew(name)}
              onCancel={() => setAdding(false)}
            />
          ) : (
            <button
              type="button"
              className="qp-profile-new-btn"
              id="qp-profile-new-btn"
              onClick={() => setAdding(true)}
            >
              + New profile
            </button>
          )}
        </div>
      </div>
      {managerModal}
    </div>
  );
}

function RankCandidates({
  hidden,
  checked,
  onChange,
}: {
  hidden: boolean;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const state = useHelp();
  const help = (
    <>
      A single source often returns several copies of the same track.
      <br />
      <br />
      <strong>Off</strong> (default): the most likely-correct / fastest copy is downloaded first —
      the original behaviour.
      <br />
      <br />
      <strong>On</strong>: the highest-quality copy (per your target list above) is downloaded first
      instead; correctness and speed only break ties.
      <br />
      <br />
      Nothing is skipped and the source order doesn't change, so a track can't go missing — it just
      prefers the better copy. (This is always how <strong>Best quality</strong> works, which is why
      this option only appears for <strong>Source priority</strong>.)
    </>
  );
  const bits = helpBits(help, state);
  return (
    <div
      className="form-group"
      id="quality-rank-candidates-group"
      style={{ display: hidden ? 'none' : '' }}
    >
      <div className="setting-row">
        <Tick
          id="quality-rank-candidates"
          label="Rank-based download order"
          style={{ margin: '0' }}
          checked={checked}
          onChange={onChange}
        />
        {bits.icon}
      </div>
      <div className="help-text setting-help-body" hidden={!state.open}>
        {help}
      </div>
      {bits.line}
    </div>
  );
}
