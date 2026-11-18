/**
 * The person editor (#2166, #2167, #2168, plan 8.2): one wide sheet per
 * profile. Header with who they follow, a rail of categories with a green
 * dot where they have changes of their own, rows drawn from the catalogue
 * with where each value comes from, and "What Maya can do" built live from
 * the draft. Save sends only the differences; the server answers with the
 * fresh layers and what changed.
 *
 * Name, picture, PIN, password, devices and the own-library folder stay in
 * the vanilla profile editor (init.js); General & account hands over to it.
 */

import { Dialog } from '@base-ui/react/dialog';
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { createRoot } from 'react-dom/client';

import { DialogFrame } from '@/components/dialog';
import { Switch } from '@/components/form/form';
import { getShellProfileContext } from '@/platform/shell/bridge';

import type { CategoryId, ProfileCard, ProfilePermissions, QualityOptions, Value } from './types';

import {
  EditorApiError,
  fetchCatalogue,
  fetchFeatures,
  fetchPermissions,
  fetchProfiles,
  fetchQuality,
  fetchTemplates,
  saveFeatures,
  savePermissions,
  saveProfile,
} from './api';
import { Icon } from './icons';
import { type LookState, SidebarLook, featuresBody, lookDirty, lookFrom } from './look';
import {
  type Draft,
  type Model,
  buildModel,
  changeCount,
  changedCategories,
  initialDraft,
  resetAll,
  resetCategory,
  resetRows,
  resolve,
  rowsIn,
  saveBody,
  setValue,
  switchTemplate,
  templateName,
  unsavedCount,
  str,
  valueWords,
} from './model';
import styles from './person-editor.module.css';
import { type EditorCtx, PermRow, QualityBlock } from './rows';
import { type EditorRequest, closePersonEditor, openPersonEditor, useEditorRequest } from './store';
import { buildSummary } from './summary';

const EMPTY_QUALITY: QualityOptions = { music: [], movies: [], tv: [] };

/** the vanilla profile functions General & account hands over to */
type Vanilla = {
  openProfileEditor?: (o: { mode: string; profile: ProfileCard; identityOnly?: boolean }) => void;
  openProfilePasswordModal?: (p: ProfileCard) => void;
  openProfileDevices?: (p: ProfileCard) => void;
  setProfileDisabled?: (p: ProfileCard, off: boolean) => void;
  _pfAfterSave?: () => void;
};
const vanilla = () => window as unknown as Vanilla;

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

type Loaded = {
  model: Model;
  card: ProfileCard | null;
  signupTid: number | null;
};

export function PersonEditorHost() {
  const request = useEditorRequest();
  return request ? <PersonEditor key={request.key} request={request} /> : null;
}

function PersonEditor({ request }: { request: EditorRequest }) {
  const { profileId, options } = request;
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<{ text: string; refused: boolean } | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [look, setLook] = useState<LookState>(() => lookFrom(null, ''));
  const [active, setActive] = useState<CategoryId | null>(options.category ?? null);
  const [announce, setAnnounce] = useState('');
  const [leaving, setLeaving] = useState<null | { after?: () => void }>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const railRef = useRef<HTMLDivElement | null>(null);
  const mainRef = useRef<HTMLDivElement | null>(null);

  const loadAll = useCallback(async () => {
    setError(null);
    try {
      const [catalogue, templates, quality, saved, profiles] = await Promise.all([
        fetchCatalogue(),
        fetchTemplates(),
        fetchQuality().catch(() => EMPTY_QUALITY),
        fetchPermissions(profileId),
        fetchProfiles().catch(() => null),
      ]);
      const model = buildModel(catalogue, templates.templates, saved, quality);
      const card = profiles?.profiles.find((p) => Number(p.id) === profileId) ?? null;
      setLoaded({ model, card, signupTid: templates.signup_template_id ?? null });
      setDraft(initialDraft(saved));
      setActive((cur) => cur ?? firstCategory(model, saved));
      const home = card?.home_page ?? '';
      if (saved.mode === 'admin') {
        setLook(lookFrom(null, home));
      } else {
        setLook(lookFrom(null, home));
        fetchFeatures(profileId)
          .then((payload) =>
            setLook((s) => ({
              ...lookFrom(payload, s.home),
              home: s.home,
              savedHome: s.savedHome,
            })),
          )
          .catch((e: unknown) =>
            setLook((s) => ({
              ...s,
              error: `Couldn't load what shows for them: ${e instanceof Error ? e.message : 'connection error'}`,
            })),
          );
      }
    } catch (e) {
      const refused = e instanceof EditorApiError && (e.status === 401 || e.status === 403);
      setError({
        text: e instanceof Error ? e.message : "Couldn't load this profile.",
        refused,
      });
    }
  }, [profileId]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const model = loaded?.model ?? null;
  const ready = !!model;
  // focus stays on the sheet itself (tab goes to the first control); once
  // it's drawn, start at its top, not wherever focusing the popup scrolled
  useEffect(() => {
    if (!ready) return;
    const frame = requestAnimationFrame(() => {
      const viewport = railRef.current?.closest('[role="dialog"]')?.parentElement;
      if (viewport) viewport.scrollTop = 0;
    });
    return () => cancelAnimationFrame(frame);
  }, [ready]);
  const resolved = useMemo(() => (model && draft ? resolve(model, draft) : null), [model, draft]);
  const name = model?.saved.profile.name ?? loaded?.card?.name ?? 'this profile';
  const legacy = !!model && !!draft && draft.templateId == null && model.saved.mode === 'legacy';
  const tplName = model && draft ? templateName(model, draft.templateId) : '';
  const unsaved = model && draft ? unsavedCount(model, draft) + (lookDirty(look) ? 1 : 0) : 0;

  const say = (text: string) => setAnnounce(text);

  const ctx: EditorCtx | null =
    model && draft && resolved
      ? {
          model,
          draft,
          resolved,
          name,
          tplName,
          legacy,
          set: (id: string, value: Value) => {
            const next = setValue(model, draft, id, value);
            setDraft(next);
            setStatus(null);
            const row = model.rowsById.get(id);
            const n = changeCount(next);
            say(
              `${row?.label ?? id}: ${row ? valueWords(model, row, value) : str(value)}. ${plural(n, 'change')} from ${templateName(model, next.templateId)}.`,
            );
          },
          reset: (ids: string[]) => {
            setDraft(resetRows(draft, ids));
            setStatus(null);
            say(`Back to ${tplName}.`);
          },
          onOpenPolicy: options.onOpenPolicy
            ? () => {
                requestClose(options.onOpenPolicy);
              }
            : undefined,
        }
      : null;

  function finish(then?: () => void) {
    closePersonEditor();
    if (then) setTimeout(then, 0);
  }

  function requestClose(then?: () => void) {
    if (saving) return;
    if (unsaved > 0) {
      setLeaving({ after: then });
      return;
    }
    finish(then);
  }

  async function save() {
    if (!model || !draft || saving) return;
    const startAdmin = initialDraft(model.saved).isAdmin;
    const adminChanged = draft.isAdmin !== startAdmin;
    setSaving(true);
    setStatus(null);
    const failed: string[] = [];
    let changes: ProfilePermissions['changes'] = [];
    let fresh: ProfilePermissions = model.saved;
    try {
      // the admin switch and what they can do go in one call, so a failure
      // never leaves them half changed
      const body = draft.isAdmin ? null : saveBody(model, draft);
      if (adminChanged || body) {
        fresh = await savePermissions(profileId, {
          ...(body ?? { set: {}, reset: [] }),
          ...(adminChanged ? { is_admin: draft.isAdmin } : {}),
        });
        changes = fresh.changes ?? [];
      }
    } catch (e) {
      setSaving(false);
      setStatus({ ok: false, text: e instanceof Error ? e.message : "That didn't save." });
      return;
    }
    let nextLook = look;
    if (!draft.isAdmin && look.payload) {
      const fb = featuresBody(look);
      if (fb) {
        try {
          const payload = await saveFeatures(profileId, fb);
          nextLook = { ...lookFrom(payload, look.home), savedHome: look.savedHome };
        } catch (e) {
          failed.push(`what shows didn't save: ${e instanceof Error ? e.message : 'error'}`);
        }
      }
    }
    if (look.home !== look.savedHome) {
      try {
        await saveProfile(profileId, { home_page: look.home || null });
        nextLook = { ...nextLook, savedHome: look.home, home: look.home };
      } catch (e) {
        failed.push(`the home page didn't save: ${e instanceof Error ? e.message : 'error'}`);
      }
    }
    const nextModel = buildModel(model.catalogue, model.templates, fresh, model.quality);
    setLoaded((l) => (l ? { ...l, model: nextModel } : l));
    setDraft(initialDraft(fresh));
    setLook(nextLook);
    setSaving(false);
    const said = changes.length
      ? `Saved. ${changes
          .slice(0, 3)
          .map((c) => {
            const row = model.rowsById.get(c.id);
            return row
              ? `${row.label}: ${valueWords(model, row, c.before)} to ${valueWords(model, row, c.after)}`
              : c.id;
          })
          .join('; ')}${changes.length > 3 ? `; and ${changes.length - 3} more` : ''}.`
      : adminChanged
        ? draft.isAdmin
          ? `Saved. ${name} is an admin now.`
          : `Saved. ${name} follows ${templateName(nextModel, initialDraft(fresh).templateId)} now.`
        : `Saved. What ${name} can do didn't change.`;
    setStatus({
      ok: !failed.length,
      text: failed.length ? `${said} But ${failed.join('; ')}.` : said,
    });
    say(said);
    window.showToast?.(
      failed.length ? `Saved ${name}, with a problem` : `Saved ${name}`,
      failed.length ? 'warning' : 'success',
    );
    options.onSaved?.(fresh);
    window.dispatchEvent(new CustomEvent('ss:person-permissions-saved', { detail: { profileId } }));
    vanilla()._pfAfterSave?.();
  }

  const onOpenChange = (open: boolean) => {
    if (!open) requestClose();
  };

  /* ── loading and refusal ─────────────────────────────────────────────── */

  if (!model || !draft || !resolved || !ctx) {
    return (
      <DialogFrame
        open
        onOpenChange={() => closePersonEditor()}
        aria-label={error ? 'Permissions' : 'Loading permissions'}
        viewportClassName={styles.viewport}
        className={styles.sheet}
      >
        <div className={styles.calm} role={error ? 'alert' : 'status'}>
          <span className={styles.calmIcon}>
            <Icon name={error?.refused ? 'lock' : error ? 'warn' : 'general'} />
          </span>
          <Dialog.Title className={styles.calmTitle}>
            {error ? (error.refused ? 'Admins only' : "Couldn't open it") : 'Loading…'}
          </Dialog.Title>
          {error ? <p>{error.text}</p> : null}
          <div className={styles.calmActions}>
            {error && !error.refused ? (
              <button type="button" className={styles.btnSecondary} onClick={() => void loadAll()}>
                Try again
              </button>
            ) : null}
            <Dialog.Close className={styles.btnSecondary}>Close</Dialog.Close>
          </div>
        </div>
      </DialogFrame>
    );
  }

  /* ── the sheet ───────────────────────────────────────────────────────── */

  const isAdmin = draft.isAdmin;
  const cats = model.catalogue.categories.filter((c) => (isAdmin ? c.id === 'general' : true));
  const current: CategoryId = cats.some((c) => c.id === active)
    ? (active as CategoryId)
    : 'general';
  const dots = changedCategories(model, draft);
  const n = changeCount(draft);
  const card = loaded?.card ?? null;
  const viewer = getShellProfileContext();
  const isOwner = profileId === 1;
  const isSelf = viewer?.profileId === profileId;
  const summary = buildSummary(model, resolved.effective, isAdmin);
  const role = isOwner ? 'Owner' : isAdmin ? 'Admin' : 'Member';
  const signIn = signInWords(card);

  const switchTo = (tid: number) => {
    const next = switchTemplate(model, draft, tid);
    setDraft(next);
    setStatus(null);
    const kept = changeCount(next);
    say(
      `Follows ${templateName(model, tid)}. ${kept ? `${plural(kept, 'change')} of ${name}'s own kept.` : 'No changes of their own.'}`,
    );
  };

  const setAdmin = (on: boolean) => {
    setStatus(null);
    if (on) {
      setDraft({ ...draft, isAdmin: true });
      say(`${name} would be an admin: everything, including settings and people.`);
      return;
    }
    let next: Draft = { ...draft, isAdmin: false };
    if (next.templateId == null && model.saved.mode === 'admin') {
      const tid = loaded?.signupTid ?? model.templates[0]?.id ?? null;
      next = { ...next, templateId: tid, overrides: {} };
    }
    setDraft(next);
    say(`${name} would be a member following ${templateName(model, next.templateId)}.`);
  };

  const onRailKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const keys = ['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'];
    if (!keys.includes(e.key)) return;
    e.preventDefault();
    const i = cats.findIndex((c) => c.id === current);
    let j = i;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') j = (i + 1) % cats.length;
    else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') j = (i - 1 + cats.length) % cats.length;
    else if (e.key === 'Home') j = 0;
    else if (e.key === 'End') j = cats.length - 1;
    setActive(cats[j].id);
    railRef.current?.querySelector<HTMLButtonElement>(`[data-cat="${cats[j].id}"]`)?.focus();
  };

  const pickCategory = (id: CategoryId) => {
    setActive(id);
    // a long pane was scrolled: show the new one from its top (on a phone,
    // from the chips, so the next pick is right there)
    const main = mainRef.current;
    if (!main || main.getBoundingClientRect().top >= 0) return;
    const narrow = window.matchMedia?.('(width <= 900px)').matches;
    const target = narrow ? railRef.current?.closest('nav') : main;
    target?.scrollIntoView?.({ block: 'start' });
  };

  const handover = (run: () => void) => requestClose(run);
  const cat = model.catalogue.categories.find((c) => c.id === current)!;
  const catChanged = dots.has(current);

  return (
    <DialogFrame
      open
      onOpenChange={onOpenChange}
      aria-label={`Edit ${name}`}
      viewportClassName={styles.viewport}
      className={styles.sheet}
    >
      <header className={styles.head}>
        <div className={styles.who}>
          <Avatar card={card} name={name} />
          <div className={styles.whoText}>
            <Dialog.Title className={styles.name}>{name}</Dialog.Title>
            <div className={styles.meta}>
              <span>
                {role}
                {!isAdmin && resolved.effective['requests.moderate'] ? ' · Moderator' : ''}
              </span>
              <span aria-hidden="true">·</span>
              <span>{signIn}</span>
              {isAdmin ? null : (
                <>
                  <span aria-hidden="true">·</span>
                  {legacy ? (
                    <span className={styles.metaWarn}>On their own settings</span>
                  ) : n ? (
                    <span className={styles.metaOn} data-testid="change-count">
                      {plural(n, 'change')} from {tplName}
                    </span>
                  ) : (
                    <span data-testid="change-count">Follows {tplName} exactly</span>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
        {isAdmin ? null : (
          <div className={styles.tpl}>
            <label className={styles.tplLabel} htmlFor={`follows-${profileId}`}>
              Follows
            </label>
            <select
              id={`follows-${profileId}`}
              className={styles.select}
              value={draft.templateId == null ? '' : String(draft.templateId)}
              onChange={(e) => e.target.value && switchTo(Number(e.target.value))}
            >
              {draft.templateId == null ? <option value="">Their own settings</option> : null}
              {model.templates.map((t) => (
                <option key={t.id} value={t.id} disabled={!t.readable}>
                  {t.name}
                </option>
              ))}
            </select>
            {legacy ? null : (
              <button
                type="button"
                className={styles.link}
                disabled={!n}
                onClick={() => {
                  setDraft(resetAll(draft));
                  setStatus(null);
                  say(`Every change for ${name} goes back to ${tplName} when you save.`);
                }}
              >
                <Icon name="undo" />
                Back to {tplName}
              </button>
            )}
          </div>
        )}
        <button
          type="button"
          className={styles.close}
          aria-label="Close"
          onClick={() => requestClose()}
        >
          <Icon name="close" />
        </button>
      </header>

      <div className={styles.body} data-admin={isAdmin || undefined}>
        <nav className={styles.railWrap} aria-label="Categories">
          <div
            className={styles.rail}
            role="tablist"
            aria-orientation="vertical"
            aria-label="Categories"
            ref={railRef}
            onKeyDown={onRailKey}
          >
            {cats.map((c) => (
              <div key={c.id} className={styles.railItem} data-look={c.id === 'look' || undefined}>
                {c.id === 'look' ? <div className={styles.railSep} aria-hidden="true" /> : null}
                <button
                  type="button"
                  role="tab"
                  id={`pe-tab-${c.id}`}
                  aria-controls={`pe-panel-${c.id}`}
                  aria-selected={c.id === current}
                  tabIndex={c.id === current ? 0 : -1}
                  className={styles.railB}
                  data-cat={c.id}
                  onClick={() => pickCategory(c.id)}
                >
                  <Icon name={c.id} />
                  <span className={styles.railLabel}>{c.label}</span>
                  {dots.has(c.id) ? (
                    <span className={styles.dot} data-dot={c.id}>
                      <span className={styles.srOnly}>, has changes</span>
                    </span>
                  ) : null}
                </button>
              </div>
            ))}
          </div>
          {isAdmin ? null : (
            <p className={styles.railNote}>A green dot means {name} has their own setting there.</p>
          )}
        </nav>

        <div
          className={styles.main}
          ref={mainRef}
          role="tabpanel"
          id={`pe-panel-${current}`}
          aria-labelledby={`pe-tab-${current}`}
          tabIndex={0}
        >
          {legacy ? (
            <div className={styles.warnNote} role="note">
              <Icon name="warn" />
              <span>
                <b>Needs a look.</b> {name} runs on their own settings from before templates
                {model.saved.needs_look.length ? ` (${model.saved.needs_look.join('; ')})` : ''}.
                Pick who they follow above and their settings come along as changes. Saving as is
                moves them onto the closest template.
              </span>
            </div>
          ) : null}
          <div className={styles.paneHead}>
            <div>
              <h2 className={styles.paneTitle}>{cat.label}</h2>
              <p className={styles.paneSub}>
                {current === 'look'
                  ? `Only what shows. Nothing here lets ${name} do more or less.`
                  : `${cat.help}${isAdmin || legacy || current === 'admin' ? '' : ` Anything not marked comes from ${tplName}.`}`}
              </p>
            </div>
            {catChanged && current !== 'look' ? (
              <button
                type="button"
                className={styles.link}
                onClick={() => {
                  setDraft(resetCategory(model, draft, current));
                  setStatus(null);
                  say(`${cat.label} back to ${tplName}.`);
                }}
              >
                <Icon name="undo" />
                Reset {cat.label}
              </button>
            ) : null}
          </div>

          {current === 'general' ? (
            <GeneralPane
              ctx={ctx}
              card={card}
              isAdmin={isAdmin}
              adminLocked={isOwner || isSelf}
              adminLockReason={
                isOwner
                  ? 'The main admin profile stays an admin.'
                  : "You can't change your own admin switch."
              }
              setAdmin={setAdmin}
              canTurnOff={!isOwner && !isSelf}
              handover={handover}
            />
          ) : current === 'look' ? (
            <SidebarLook
              model={model}
              eff={resolved.effective}
              name={name}
              state={look}
              onChange={(s) => {
                setLook(s);
                setStatus(null);
              }}
            />
          ) : (
            <CategoryPane ctx={ctx} cat={current} />
          )}
        </div>

        <aside className={styles.summary} aria-label={`What ${name} can do`}>
          <h3 className={styles.sumT}>What {name} can do</h3>
          <p className={styles.sumLine} data-testid="summary-sentence">
            {summary.sentence.map((b, i) =>
              b.strong ? <em key={i}>{b.text}</em> : <span key={i}>{b.text}</span>,
            )}
          </p>
          {summary.lines.map((l) => (
            <div key={l.title} className={styles.sumRow} data-sum={l.icon}>
              <span className={styles.sumI}>
                <Icon name={l.icon} />
              </span>
              <div>
                <div className={styles.sumK}>{l.title}</div>
                <div className={styles.sumV}>
                  {l.bits.map((b, i) => (
                    <span key={i}>
                      {i ? ' · ' : ''}
                      {b.good ? <b>{b.text}</b> : b.text}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
          {summary.never.length ? (
            <div className={styles.cant} data-testid="summary-never">
              <b>Never:</b> {summary.never.join(', ')}.
            </div>
          ) : null}
        </aside>
      </div>

      <footer className={styles.foot}>
        {leaving ? (
          <>
            <span className={styles.footText} role="alert">
              Leave without saving {plural(unsaved, 'change')}?
            </span>
            <span className={styles.spacer} />
            <button type="button" className={styles.btnSecondary} onClick={() => setLeaving(null)}>
              Keep editing
            </button>
            <button
              type="button"
              className={styles.btnDanger}
              onClick={() => {
                const after = leaving.after;
                setLeaving(null);
                finish(after);
              }}
            >
              Discard
            </button>
          </>
        ) : (
          <>
            <span
              className={styles.footText}
              data-state={status ? (status.ok ? 'ok' : 'error') : undefined}
              data-idle={!saving && !status && !unsaved ? true : undefined}
            >
              {saving
                ? 'Saving…'
                : status
                  ? status.text
                  : unsaved
                    ? `${plural(unsaved, 'unsaved change')}. Saved changes reach ${name} right away.`
                    : `Saved changes reach ${name} right away. Pages they can't use refuse them, not just hide.`}
            </span>
            <span className={styles.spacer} />
            <button type="button" className={styles.btnSecondary} onClick={() => requestClose()}>
              {unsaved ? 'Cancel' : 'Done'}
            </button>
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={!unsaved || saving}
              onClick={() => void save()}
            >
              Save
            </button>
          </>
        )}
      </footer>
      <div className={styles.srOnly} role="status" aria-live="polite" data-testid="announce">
        {announce}
      </div>
    </DialogFrame>
  );
}

function firstCategory(model: Model, saved: ProfilePermissions): CategoryId {
  if (saved.mode === 'admin') return 'general';
  const changed = model.catalogue.categories.find((c) => saved.changed_categories.includes(c.id));
  return changed?.id ?? 'general';
}

function Avatar({ card, name }: { card: ProfileCard | null; name: string }) {
  const [broken, setBroken] = useState(false);
  if (card?.avatar_url && !broken) {
    return (
      <img className={styles.avatar} src={card.avatar_url} alt="" onError={() => setBroken(true)} />
    );
  }
  return (
    <span
      className={styles.avatar}
      style={card?.avatar_color ? { background: card.avatar_color } : undefined}
      aria-hidden="true"
    >
      {(name || '?').trim().charAt(0).toUpperCase()}
    </span>
  );
}

/* ── panes ─────────────────────────────────────────────────────────────── */

function CategoryPane({ ctx, cat }: { ctx: EditorCtx; cat: CategoryId }) {
  const rows = rowsIn(ctx.model, cat).filter(
    (r) => !r.presentation && r.kind !== 'admin' && r.kind !== 'choice' && r.kind !== 'set_flag',
  );
  const plain = rows.filter((r) => !r.group && !r.media);
  const media = rows.find((r) => r.kind === 'set' && r.media)?.media ?? null;
  const groups = [...new Set(rows.filter((r) => r.group).map((r) => r.group))];
  const otherName = (r: (typeof rows)[number]) =>
    r.also_in?.length
      ? cat === r.category
        ? `Shared with ${ctx.model.catalogue.categories.find((c) => c.id === r.also_in[0])?.label ?? ''}.`
        : `Shared with ${ctx.model.catalogue.categories.find((c) => c.id === r.category)?.label ?? ''}.`
      : undefined;
  return (
    <>
      {cat === 'admin' ? (
        <div className={styles.infoNote} role="note">
          <Icon name="info" />
          <span>
            Settings, people, tools jobs, automations for everyone, server activity and logs come
            with the <b>Admin</b> switch in General &amp; account. {ctx.name} isn&apos;t an admin,
            so the server refuses them.
          </span>
        </div>
      ) : null}
      {plain.length ? (
        <div className={styles.group}>
          {plain.map((r) => (
            <PermRow key={r.id} ctx={ctx} row={r} />
          ))}
        </div>
      ) : null}
      {media ? (
        <>
          <h3 className={styles.sect}>Quality {ctx.name} can ask for</h3>
          <QualityBlock ctx={ctx} media={media} />
        </>
      ) : null}
      {groups.map((g) => (
        <section key={g} aria-label={g}>
          <h3 className={styles.sect}>{g}</h3>
          <div className={styles.group}>
            {rows
              .filter((r) => r.group === g)
              .map((r) => (
                <PermRow key={r.id} ctx={ctx} row={r} shared={otherName(r)} />
              ))}
          </div>
        </section>
      ))}
    </>
  );
}

function GeneralPane({
  ctx,
  card,
  isAdmin,
  adminLocked,
  adminLockReason,
  setAdmin,
  canTurnOff,
  handover,
}: {
  ctx: EditorCtx;
  card: ProfileCard | null;
  isAdmin: boolean;
  adminLocked: boolean;
  adminLockReason: string;
  setAdmin: (on: boolean) => void;
  canTurnOff: boolean;
  handover: (run: () => void) => void;
}) {
  const adminRow = ctx.model.rowsById.get('account.admin');
  const rows = rowsIn(ctx.model, 'general').filter((r) => r.kind !== 'admin');
  const v = vanilla();
  return (
    <>
      <div className={styles.group}>
        <div className={styles.row} data-row="account.admin">
          <div className={styles.rowText}>
            <div className={styles.rowTitle} id="pe-admin-label">
              Admin
            </div>
            <div className={styles.rowDesc}>
              {adminRow?.help ?? 'Everything, including settings and people.'} Turning this on hides
              every other category.
            </div>
            {adminLocked ? (
              <div className={styles.prov}>
                <span className={styles.lock}>
                  <Icon name="lock" />
                  {adminLockReason}
                </span>
              </div>
            ) : null}
          </div>
          <div className={styles.rowCtl}>
            <Switch
              aria-labelledby="pe-admin-label"
              checked={isAdmin}
              disabled={adminLocked}
              onCheckedChange={setAdmin}
            />
          </div>
        </div>
        {isAdmin ? (
          <p className={styles.rowNote}>
            Admins can use everything, including settings and people.
          </p>
        ) : (
          rows.map((r) => <PermRow key={r.id} ctx={ctx} row={r} />)
        )}
      </div>

      {card ? (
        <>
          <h3 className={styles.sect}>Account</h3>
          <div className={styles.group}>
            <ActionRow
              title="Name, picture and PIN"
              desc={`${card.has_pin ? 'PIN set.' : 'No PIN.'} Opens their profile card editor.`}
              action="Edit"
              onClick={
                v.openProfileEditor
                  ? () =>
                      handover(() =>
                        v.openProfileEditor?.({ mode: 'edit', profile: card, identityOnly: true }),
                      )
                  : undefined
              }
            />
            {isAdmin ? null : (
              <ActionRow
                title="Login password"
                desc={card.has_password ? 'Set.' : 'Not set. They need one when login mode is on.'}
                action={card.has_password ? 'Change' : 'Set one'}
                onClick={
                  v.openProfilePasswordModal
                    ? () => handover(() => v.openProfilePasswordModal?.(card))
                    : undefined
                }
              />
            )}
            {isAdmin ? null : (
              <ActionRow
                title="Own library folder"
                desc={
                  card.library_mode === 'own'
                    ? `Their own: ${card.library_root || 'no folder yet'}.`
                    : 'The shared library.'
                }
                action="Change"
                onClick={
                  v.openProfileEditor
                    ? () =>
                        handover(() =>
                          v.openProfileEditor?.({
                            mode: 'edit',
                            profile: card,
                            identityOnly: true,
                          }),
                        )
                    : undefined
                }
              />
            )}
            <ActionRow
              title="Devices"
              desc="Every browser and phone signed in as them."
              action="See them"
              onClick={
                v.openProfileDevices
                  ? () => handover(() => v.openProfileDevices?.(card))
                  : undefined
              }
            />
            {canTurnOff ? (
              <ActionRow
                title={card.disabled ? 'Turn this profile back on' : 'Turn off this profile'}
                desc={
                  card.disabled
                    ? 'Nobody can open it right now. Everything is kept.'
                    : "Everything is kept. They're signed out everywhere."
                }
                action={card.disabled ? 'Turn on' : 'Turn off'}
                onClick={
                  v.setProfileDisabled
                    ? () => handover(() => v.setProfileDisabled?.(card, !card.disabled))
                    : undefined
                }
              />
            ) : null}
          </div>
        </>
      ) : null}
    </>
  );
}

function ActionRow({
  title,
  desc,
  action,
  onClick,
}: {
  title: string;
  desc: string;
  action: string;
  onClick?: () => void;
}) {
  return (
    <div className={styles.row}>
      <div className={styles.rowText}>
        <div className={styles.rowTitle}>{title}</div>
        <div className={styles.rowDesc}>{desc}</div>
      </div>
      <div className={styles.rowCtl}>
        {onClick ? (
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={onClick}
            aria-label={`${action}: ${title}`}
          >
            {action}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/* ── mount ─────────────────────────────────────────────────────────────── */

const HOST_ID = 'soulsync-person-editor-root';

/** mount once; the vanilla profile cards open it through the window global */
export function mountPersonEditorHost(): void {
  if (typeof document === 'undefined' || document.getElementById(HOST_ID)) return;
  const el = document.createElement('div');
  el.id = HOST_ID;
  document.body.appendChild(el);
  createRoot(el).render(<PersonEditorHost />);
  window.openPersonEditor = openPersonEditor;
  window.closePersonEditor = closePersonEditor;
}

/** the header's "Signs in with Plex": the server's signin, else the old guess */
function signInWords(card: ProfileCard | null): string {
  switch (card?.signin) {
    case 'oidc':
      return 'Signs in with OIDC';
    case 'plex':
      return 'Signs in with Plex';
    case 'password':
      return 'Signs in with a password';
    case 'pin':
      return 'Opens with a PIN';
    case 'picker':
      return 'Opens from the profile picker';
    default:
      return card?.has_password
        ? 'Signs in with a password'
        : card?.has_pin
          ? 'Opens with a PIN'
          : 'Opens from the profile picker';
  }
}
