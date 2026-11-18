/**
 * Templates: a card per template with who follows it, then the selected
 * one's rows (the editor's rail and rows, minus anything personal) and its
 * Sidebar & look. Rename, Duplicate, New and Delete (with somewhere to move
 * its followers first), and "New people start as", the sign-up template.
 *
 * Sidebar & look replaces the old "What new profiles see": every template
 * carries its own sidebar, so there is one place for it.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import type { PeopleData } from './data';
import type { FeatureCatalogue, PermMap, PermValue, Sidebar, Template } from './types';

import {
  createTemplate,
  deleteTemplate,
  getFeatureCatalogue,
  putSignupTemplate,
  putTemplate,
  type TemplateBody,
} from './api';
import { CategoryRows, Rail, SaveBar } from './controls';
import { Icon } from './icons';
import { categoriesFor, changedRows, listWords, rowsFor, sameValue } from './model';
import css from './people.module.css';
import { SidebarLook } from './sidebar-look';
import { useDryRun } from './use-dry-run';

const toast = (message: string, type = 'success') => window.showToast?.(message, type);

function uniqueName(base: string, templates: Template[]): string {
  const names = new Set(templates.map((t) => t.name.toLowerCase()));
  if (!names.has(base.toLowerCase())) return base;
  for (let i = 2; i < 100; i += 1) {
    const name = `${base} ${i}`;
    if (!names.has(name.toLowerCase())) return name;
  }
  return `${base} ${Date.now()}`;
}

function followersLine(t: Template, data: PeopleData): string {
  const names = t.followers.map((f) => f.name);
  if (!names.length) return 'Nobody follows it yet. Changes reach new people who start on it.';
  const who =
    names.length > 4
      ? `${names.slice(0, 3).join(', ')} and ${names.length - 3} others`
      : listWords(names);
  const own = t.followers.reduce((n, f) => {
    const row = data.people.find((p) => p.person.id === f.id);
    return (
      n + (row?.perms?.mode === 'template' ? Object.keys(row.perms.overrides || {}).length : 0)
    );
  }, 0);
  const kept = own
    ? ` ${own} personal ${own === 1 ? 'change stays' : 'changes stay'} as ${own === 1 ? 'it is' : 'they are'}.`
    : '';
  return `Changes reach ${who} now.${kept}`;
}

function Faces({ t, data }: { t: Template; data: PeopleData }) {
  const color = (id: number) => data.people.find((p) => p.person.id === id)?.person.avatar_color;
  return (
    <span className={css.tcardF}>
      {t.followers.length ? (
        <span className={css.faces} aria-hidden="true">
          {t.followers.slice(0, 4).map((f) => (
            <span key={f.id} style={{ ['--av' as string]: color(f.id) || undefined }}>
              {f.name.charAt(0).toUpperCase()}
            </span>
          ))}
        </span>
      ) : null}
      {t.followers.length
        ? `${t.followers.length} ${t.followers.length === 1 ? 'person' : 'people'}`
        : 'Nobody yet'}
    </span>
  );
}

const sidebarOf = (t: Template | undefined): Sidebar => ({
  off: [...(t?.sidebar?.off ?? [])],
  locked: !!t?.sidebar?.locked,
});
const sameSidebar = (a: Sidebar, b: Sidebar) =>
  a.locked === b.locked && sameValue(a.off as PermValue, b.off as PermValue);

export function TemplatesScreen({ data, onSaved }: { data: PeopleData; onSaved: () => void }) {
  const { catalogue, templates } = data;
  const categories = useMemo(() => categoriesFor(catalogue, 'T'), [catalogue]);
  const [selectedId, setSelectedId] = useState<number | null>(templates[0]?.id ?? null);
  const selected = templates.find((t) => t.id === selectedId) ?? templates[0];
  const [category, setCategory] = useState('movies');
  const [draft, setDraft] = useState<PermMap>(selected?.perms ?? {});
  const [sidebar, setSidebar] = useState<Sidebar>(sidebarOf(selected));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{ name: string; description: string } | null>(null);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<{ to: number | null; error: string | null } | null>(
    null,
  );
  const [features, setFeatures] = useState<FeatureCatalogue | null>(null);
  const [featuresError, setFeaturesError] = useState<string | null>(null);
  const [signupError, setSignupError] = useState<string | null>(null);
  const renameRef = useRef<HTMLInputElement>(null);

  // another template, or a save, starts the draft from what is saved; a
  // reload that changed nothing here keeps what is being edited
  const savedKey = JSON.stringify([selected?.id, selected?.perms, selected?.sidebar]);
  useEffect(() => {
    const [, perms, side] = JSON.parse(savedKey) as [number, PermMap | null, Sidebar | null];
    setDraft(perms ?? {});
    setSidebar({ off: [...(side?.off ?? [])], locked: !!side?.locked });
    setSaveError(null);
  }, [savedKey]);
  useEffect(() => {
    if (renaming) renameRef.current?.focus();
  }, [renaming]);
  useEffect(() => {
    if (category !== 'look' || features) return;
    const ctrl = new AbortController();
    getFeatureCatalogue(ctrl.signal)
      .then((r) => {
        if (r.ok) setFeatures(r.data);
        else setFeaturesError(r.message);
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [category, features]);

  const saved = selected?.perms ?? {};
  const changed = useMemo(() => changedRows(saved, draft), [saved, draft]);
  const sidebarChanged = !!selected && !sameSidebar(sidebarOf(selected), sidebar);
  const dirty = Object.keys(changed).length > 0 || sidebarChanged;
  const body: TemplateBody | null = dirty
    ? {
        ...(Object.keys(changed).length ? { perms: changed } : {}),
        ...(sidebarChanged ? { sidebar } : {}),
      }
    : null;
  const dry = useDryRun(body, (b) => putTemplate(selected!.id, b, true));

  const unsaved = useMemo(() => {
    const out = new Set<string>();
    for (const c of categories) {
      if (c.id === 'look') {
        if (sidebarChanged) out.add('look');
      } else if (rowsFor(catalogue, c.id, 'T').some((r) => r.id in changed)) out.add(c.id);
    }
    return out;
  }, [catalogue, categories, changed, sidebarChanged]);

  const pick = async (id: number) => {
    if (id === selected?.id) return;
    if (dirty) {
      const ok = await (window.showConfirmDialog?.({
        title: `Leave ${selected?.name} without saving?`,
        message: 'The changes you made to it are dropped.',
        confirmText: 'Drop them',
        destructive: true,
      }) ?? Promise.resolve(true));
      if (!ok) return;
    }
    setRenaming(null);
    setDeleting(null);
    setSelectedId(id);
  };

  const create = async (from?: Template) => {
    const name = uniqueName(from ? `${from.name} copy` : 'New template', templates);
    const r = await createTemplate({
      name,
      ...(from ? { from_template_id: from.id, description: from.description ?? undefined } : {}),
    });
    if (!r.ok) {
      toast(r.message, 'error');
      return;
    }
    toast(from ? `Made ${name}` : 'New template made from Adult');
    setSelectedId(r.data.template.id);
    setRenaming({ name: r.data.template.name, description: r.data.template.description ?? '' });
    onSaved();
  };

  const rename = async () => {
    if (!selected || !renaming) return;
    const name = renaming.name.trim();
    if (!name) {
      setRenameError('Give it a name.');
      return;
    }
    const r = await putTemplate(selected.id, { name, description: renaming.description.trim() });
    if (!r.ok) {
      setRenameError(
        r.code === 'duplicate_name' ? `There's already a template called ${name}.` : r.message,
      );
      return;
    }
    setRenaming(null);
    setRenameError(null);
    onSaved();
  };

  const remove = async (reassignTo?: number) => {
    if (!selected) return;
    const r = await deleteTemplate(selected.id, reassignTo);
    if (!r.ok) {
      if (r.code === 'has_followers') {
        setDeleting({ to: null, error: 'People still follow it. Pick where they go first.' });
      } else if (r.code === 'signup_template') {
        setDeleting({
          to: null,
          error: `New people start as ${selected.name}. Pick another sign-up template first.`,
        });
      } else setDeleting({ to: deleting?.to ?? null, error: r.message });
      return;
    }
    const moved = r.data.moved.map((m) => m.name);
    toast(
      moved.length
        ? `Deleted ${selected.name}. ${listWords(moved)} moved.`
        : `Deleted ${selected.name}`,
    );
    setDeleting(null);
    setSelectedId(templates.find((t) => t.id !== selected.id)?.id ?? null);
    onSaved();
  };

  const startDelete = async () => {
    if (!selected) return;
    if (selected.id === data.signupTemplateId) {
      setDeleting({
        to: null,
        error: `New people start as ${selected.name}. Pick another sign-up template first.`,
      });
      return;
    }
    if (selected.followers.length) {
      setDeleting({ to: templates.find((t) => t.id !== selected.id)?.id ?? null, error: null });
      return;
    }
    const ok = await (window.showConfirmDialog?.({
      title: `Delete ${selected.name}?`,
      message: 'Nobody follows it, so nobody changes. This cannot be undone.',
      confirmText: 'Delete',
      destructive: true,
    }) ?? Promise.resolve(true));
    if (ok) await remove();
  };

  const setSignup = async (id: number) => {
    setSignupError(null);
    const r = await putSignupTemplate(id);
    if (!r.ok) {
      setSignupError(r.message);
      return;
    }
    toast(`New people start as ${templates.find((t) => t.id === id)?.name ?? 'that'}`);
    onSaved();
  };

  const save = async () => {
    if (!selected || !body) return;
    setSaving(true);
    setSaveError(null);
    const r = await putTemplate(selected.id, body);
    setSaving(false);
    if (!r.ok) {
      setSaveError(r.message);
      return;
    }
    toast(`${selected.name} saved`);
    onSaved();
  };

  const currentCat = categories.find((c) => c.id === category) ?? categories[0];
  const others = templates.filter((t) => t.id !== selected?.id);

  return (
    <>
      <div className={css.tcards}>
        {templates.map((t) => (
          <button
            key={t.id}
            type="button"
            className={css.tcard}
            aria-pressed={t.id === selected?.id}
            onClick={() => void pick(t.id)}
          >
            <span className={css.tcardN}>
              {t.name}
              {t.id === data.signupTemplateId ? (
                <span className={`${css.tag} ${css.tagAccent}`}>Sign-up</span>
              ) : null}
            </span>
            <span className={css.tcardD}>{t.description || 'Your own template.'}</span>
            <Faces t={t} data={data} />
          </button>
        ))}
        <button
          type="button"
          className={`${css.tcard} ${css.tcardNew}`}
          onClick={() => void create()}
        >
          <Icon name="plus" />
          New template
        </button>
      </div>

      <div className={css.starts}>
        <label>
          <b>New people start as</b>
          <select
            className={css.sel}
            value={data.signupTemplateId ?? ''}
            onChange={(e) => void setSignup(Number(e.target.value))}
          >
            {data.signupTemplateId == null ? <option value="">Pick one</option> : null}
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <span className={`${css.t2} ${css.wrap}`}>
          when they sign in with Plex or OIDC. Invites pick their own template.
        </span>
        {signupError ? <span className={css.error}>{signupError}</span> : null}
      </div>

      {selected && currentCat ? (
        <div className={css.split}>
          <Rail
            label="Template categories"
            idPrefix="ppl-tpl"
            categories={categories}
            current={currentCat.id}
            marked={unsaved}
            markTitle="Not saved yet"
            onSelect={setCategory}
          />
          <div id="ppl-tpl-pane" role="tabpanel" aria-labelledby={`ppl-tpl-tab-${currentCat.id}`}>
            <div className={css.paneHead}>
              {renaming ? (
                <div className={css.inline}>
                  <input
                    ref={renameRef}
                    className={css.field}
                    aria-label="Template name"
                    maxLength={60}
                    value={renaming.name}
                    onChange={(e) => setRenaming({ ...renaming, name: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void rename();
                      if (e.key === 'Escape') setRenaming(null);
                    }}
                  />
                  <input
                    className={css.field}
                    aria-label="What it's for"
                    placeholder="What it's for (optional)"
                    maxLength={200}
                    style={{ flex: '1 1 260px' }}
                    value={renaming.description}
                    onChange={(e) => setRenaming({ ...renaming, description: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void rename();
                      if (e.key === 'Escape') setRenaming(null);
                    }}
                  />
                  <button
                    type="button"
                    className={`${css.btn} ${css.primary}`}
                    onClick={() => void rename()}
                  >
                    Save name
                  </button>
                  <button
                    type="button"
                    className={`${css.btn} ${css.secondary}`}
                    onClick={() => {
                      setRenaming(null);
                      setRenameError(null);
                    }}
                  >
                    Cancel
                  </button>
                  {renameError ? (
                    <span className={css.error} role="alert">
                      {renameError}
                    </span>
                  ) : null}
                </div>
              ) : (
                <>
                  <div>
                    <h2 className={css.h2}>{selected.name}</h2>
                    <p className={css.paneSub}>{followersLine(selected, data)}</p>
                  </div>
                  <button
                    type="button"
                    className={css.link}
                    onClick={() =>
                      setRenaming({ name: selected.name, description: selected.description ?? '' })
                    }
                  >
                    Rename
                  </button>
                  <button type="button" className={css.link} onClick={() => void create(selected)}>
                    Duplicate
                  </button>
                  <button type="button" className={css.link} onClick={() => void startDelete()}>
                    Delete
                  </button>
                </>
              )}
            </div>

            {deleting ? (
              <div className={css.reassign} role="alert">
                {deleting.error && !selected.followers.length ? (
                  <span>{deleting.error}</span>
                ) : (
                  <>
                    <span>
                      {deleting.error ? `${deleting.error} ` : ''}
                      <b>{listWords(selected.followers.map((f) => f.name))}</b>{' '}
                      {selected.followers.length === 1 ? 'follows' : 'follow'} {selected.name}. Move{' '}
                      {selected.followers.length === 1 ? 'them' : 'them all'} to another template
                      first; their own changes go with them.
                    </span>
                    <select
                      className={css.sel}
                      aria-label="Move them to"
                      value={deleting.to ?? ''}
                      onChange={(e) => setDeleting({ ...deleting, to: Number(e.target.value) })}
                    >
                      {others.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className={`${css.btn} ${css.danger}`}
                      disabled={!deleting.to}
                      onClick={() => void remove(deleting.to ?? undefined)}
                    >
                      Move and delete
                    </button>
                  </>
                )}
                <button
                  type="button"
                  className={`${css.btn} ${css.secondary}`}
                  onClick={() => setDeleting(null)}
                >
                  Cancel
                </button>
              </div>
            ) : null}

            {!selected.readable || !selected.perms ? (
              <div className={`${css.note} ${css.noteInfo}`}>
                <Icon name="warn" />
                <span>
                  <b>This template can&apos;t be read.</b> Its followers can&apos;t be given
                  anything until it is fixed, so they get nothing for now. Move them to another
                  template, then delete it.
                </span>
              </div>
            ) : currentCat.id === 'look' ? (
              <SidebarLook
                template={selected}
                perms={draft}
                catalogue={catalogue}
                features={features}
                error={featuresError}
                value={sidebar}
                onChange={setSidebar}
              />
            ) : (
              <CategoryRows
                catalogue={catalogue}
                category={currentCat.id}
                layer="T"
                values={draft}
                ceiling={data.policy}
                quality={data.quality}
                onChange={(id: string, v: PermValue) => setDraft((d) => ({ ...d, [id]: v }))}
              />
            )}

            <SaveBar
              dirty={dirty}
              checking={dry.checking}
              impact={dry.impact}
              catalogue={catalogue}
              quality={data.quality}
              scope="template"
              error={saveError ?? dry.error}
              saving={saving}
              saveLabel={`Save ${selected.name}`}
              onDiscard={() => {
                setDraft(selected.perms ?? {});
                setSidebar(sidebarOf(selected));
                setSaveError(null);
              }}
              onSave={() => void save()}
            />
          </div>
        </div>
      ) : (
        <p className={css.empty}>No templates yet. Make one to start.</p>
      )}
    </>
  );
}
