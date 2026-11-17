/**
 * Home, top to bottom (plan 4.2): the admin's status card, On this page
 * (pages/*.ts), Read more, the More tiles, and Get help. A member gets no
 * status, no Logs and no Report a problem; their Something wrong? list
 * points at the Issues they already have (plan 4.10).
 */

import type { PageHeading, UseAnswers, UseHeading } from './pages';
import type { ViewProps } from './view-props';

import './pages/register';
import s from './help.module.css';
import { Icon } from './icons';
import {
  DISCORD_URL,
  GITHUB_ISSUES_URL,
  goToPage,
  openArticleInSheet,
  openDocsHome,
  openExternal,
} from './links';
import { isVideoSide } from './page';
import { articlesFor, pageHelpFor, type AnswerContext, type PageHelp } from './pages';
import { useHelpStore } from './store';
import { Label, List, Row, Tile } from './ui';

/** the version the What's new chip compares against (helper.js used the same key) */
export const VERSION_SEEN_KEY = 'soulsync_helper_version_seen';

export function readSeenVersion(): string | null {
  try {
    return localStorage.getItem(VERSION_SEEN_KEY);
  } catch {
    return null;
  }
}

export function markVersionSeen(version: string | null | undefined): void {
  if (!version) return;
  try {
    localStorage.setItem(VERSION_SEEN_KEY, version);
  } catch {
    /* storage blocked: the chip just stays */
  }
}

/** dispatched for P6's tours; nothing listens until they land */
export const START_TOUR_EVENT = 'ss:help-start-tour';

export function HomeView({ isAdmin, page }: ViewProps) {
  const entry = pageHelpFor(page);
  return (
    <>
      {isAdmin ? <StatusCard /> : null}
      <Label>On this page</Label>
      <OnThisPage entry={entry} isAdmin={isAdmin} page={page} />
      <ReadMore entry={entry} isAdmin={isAdmin} />
      <Label>More</Label>
      <MoreTiles isAdmin={isAdmin} />
      {isAdmin ? <AdminGetHelp /> : <MemberGetHelp />}
    </>
  );
}

// ── status (admin) ──────────────────────────────────────────────────────

function StatusCard() {
  const status = useHelpStore((st) => st.status);
  const failed = useHelpStore((st) => st.statusError);
  const push = useHelpStore((st) => st.push);
  const open = () => push({ view: 'health' });

  let tone: 'warn' | 'ok' | 'quiet' = 'quiet';
  let title = 'Checking SoulSync';
  let detail = 'Services, folders, disk and workers.';
  let icon: 'alert' | 'check' | 'pulse' = 'pulse';
  if (status && status.errors > 0) {
    tone = 'warn';
    icon = 'alert';
    title = status.errors === 1 ? '1 thing needs you' : `${status.errors} things need you`;
    detail = sentence(status.top.filter((f) => f.severity === 'error').map((f) => f.title));
  } else if (status && status.refreshing && !status.checked_at) {
    // the server's first run is still going: say so, never "all fine"
  } else if (status) {
    tone = 'ok';
    icon = 'check';
    title = 'Everything is running';
    detail = status.warnings
      ? status.warnings === 1
        ? '1 thing is worth a look.'
        : `${status.warnings} things are worth a look.`
      : 'Nothing needs you right now.';
  } else if (failed) {
    title = 'Health';
    detail = "Couldn't check just now. Open it to try again.";
  }

  return (
    <button type="button" className={s.status} data-tone={tone} onClick={open}>
      <span className={s.sIcon} aria-hidden="true">
        <Icon name={icon} size={16} />
      </span>
      <span className={s.sText}>
        <span className={s.sTitle}>{title}</span>
        <span className={s.sDetail}>{detail}</span>
      </span>
      <span className={s.sMore}>
        {tone === 'warn' ? 'See all' : 'Health'}
        <Icon name="chev" size={14} />
      </span>
    </button>
  );
}

/** "Jellyfin isn't answering, and /downloads isn't writable." */
function sentence(titles: string[]): string {
  const shown = titles.filter(Boolean).slice(0, 2);
  if (!shown.length) return 'Open Health to see what and how to fix it.';
  const lower = (t: string, i: number) => (i === 0 ? t : t.charAt(0).toLowerCase() + t.slice(1));
  const more = titles.length > 2 ? `, and ${titles.length - 2} more` : '';
  const body =
    shown.length === 2 && !more
      ? `${shown[0]}, and ${lower(shown[1], 1)}`
      : shown.map(lower).join(', ') + more;
  return /[.!?]$/.test(body) ? body : `${body}.`;
}

// ── on this page ────────────────────────────────────────────────────────

function OnThisPage(props: { entry: PageHelp; isAdmin: boolean; page: string }) {
  const { entry } = props;
  // keyed by page, as the answers are: a new page is a new component
  return entry.useHeading ? (
    <LiveHere key={entry.id} useHeading={entry.useHeading} {...props} />
  ) : (
    <Here heading={null} {...props} />
  );
}

function LiveHere({
  useHeading,
  ...props
}: {
  useHeading: UseHeading;
  entry: PageHelp;
  isAdmin: boolean;
  page: string;
}) {
  const heading = useHeading({ page: props.page, isAdmin: props.isAdmin });
  return <Here heading={heading} {...props} />;
}

function Here({
  entry,
  isAdmin,
  page,
  heading,
}: {
  entry: PageHelp;
  isAdmin: boolean;
  page: string;
  heading: PageHeading | null;
}) {
  const title = heading?.title || entry.title;
  const summary = (!isAdmin && entry.memberSummary) || heading?.summary || entry.summary;
  return (
    <section className={s.here} aria-label={`On this page: ${title}`}>
      <div className={s.hereTop}>
        <span className={s.hereIcon} aria-hidden="true">
          <Icon name={entry.icon} size={16} />
        </span>
        <div>
          <h4 className={s.hereTitle}>{title}</h4>
          {summary ? <p className={s.hereDesc}>{summary}</p> : null}
        </div>
      </div>
      {entry.useAnswers ? (
        // keyed by page: a new page is a new component, so its hook order is its own
        <Answers key={entry.id} useAnswers={entry.useAnswers} ctx={{ page, isAdmin }} />
      ) : null}
    </section>
  );
}

function Answers({ useAnswers, ctx }: { useAnswers: UseAnswers; ctx: AnswerContext }) {
  const answers = useAnswers(ctx);
  return (
    <>
      {answers.map((a) => (
        <details key={a.id} className={s.answer} data-tone={a.tone} open={a.open}>
          <summary className={s.answerSummary}>
            <span className={s.chev} aria-hidden="true">
              <Icon name="chev" size={14} />
            </span>
            <span className={s.answerTitle}>{a.title}</span>
            {a.tone === 'warn' ? (
              <span className={s.answerTag}>Needs you</span>
            ) : a.live ? (
              <span className={s.answerTag}>Live</span>
            ) : null}
          </summary>
          <div className={s.answerBody}>{a.body}</div>
        </details>
      ))}
    </>
  );
}

function ReadMore({ entry, isAdmin }: { entry: PageHelp; isAdmin: boolean }) {
  const articles = articlesFor(entry, isAdmin).slice(0, 3);
  const tour = entry.tour;
  if (!articles.length && !tour) return null;
  return (
    <>
      <Label>Read more</Label>
      <List label="Read more">
        {articles.map((a) => (
          <Row
            key={a.id}
            icon="doc"
            title={a.title}
            sub={a.lede}
            end="chev"
            onClick={() => openArticleInSheet(a.id)}
          />
        ))}
        {tour ? (
          <Row
            icon="play"
            title={`Tour: ${tour.title}`}
            sub={tour.detail}
            end="chev"
            onClick={() => {
              useHelpStore.getState().close();
              window.dispatchEvent(new CustomEvent(START_TOUR_EVENT, { detail: { id: tour.id } }));
            }}
          />
        ) : null}
      </List>
    </>
  );
}

// ── more ────────────────────────────────────────────────────────────────

function MoreTiles({ isAdmin }: { isAdmin: boolean }) {
  const version = useHelpStore((st) => st.update?.current_version ?? null);
  const push = useHelpStore((st) => st.push);
  const unseen = !!version && readSeenVersion() !== version;
  return (
    <div className={s.tiles} data-count={isAdmin ? 4 : 3}>
      <Tile
        icon="spark"
        label="What's new"
        chip={unseen ? version : null}
        chipLabel={unseen ? `${version} is new` : undefined}
        onClick={() => {
          markVersionSeen(version);
          push({ view: 'whats-new' });
        }}
      />
      <Tile icon="key" label="Shortcuts" onClick={() => push({ view: 'shortcuts' })} />
      <Tile icon="book" label="Help & Docs" onClick={openDocsHome} />
      {isAdmin ? (
        <Tile icon="terminal" label="Logs" onClick={() => push({ view: 'logs' })} />
      ) : null}
    </div>
  );
}

// ── get help ────────────────────────────────────────────────────────────

function AdminGetHelp() {
  const push = useHelpStore((st) => st.push);
  return (
    <>
      <Label>Get help</Label>
      <List label="Get help">
        <Row
          icon="bug"
          title="Report a problem"
          sub="With diagnostics you can read first, secrets removed"
          end="chev"
          onClick={() => push({ view: 'report' })}
        />
        <Row
          icon="chat"
          title="Ask on Discord"
          sub="The SoulSync community"
          end="tab"
          onClick={() => openExternal(DISCORD_URL)}
        />
        <Row
          icon="git"
          title="GitHub issues"
          sub="Bugs and feature requests"
          end="tab"
          onClick={() => openExternal(GITHUB_ISSUES_URL)}
        />
      </List>
    </>
  );
}

function MemberGetHelp() {
  const issues = isVideoSide() ? 'video-issues' : 'issues';
  return (
    <>
      <Label>Something wrong?</Label>
      <List label="Something wrong?">
        <Row
          icon="flag"
          title="Report a song, album or title"
          sub="Use Report on its ⋯ menu. It goes to your admin."
        />
        <Row
          icon="list"
          title="Your reports"
          sub="What you reported and what happened"
          end="chev"
          onClick={() => {
            useHelpStore.getState().close();
            goToPage(issues);
          }}
        />
        <Row
          icon="chat"
          title="Ask on Discord"
          sub="The SoulSync community"
          end="tab"
          onClick={() => openExternal(DISCORD_URL)}
        />
      </List>
    </>
  );
}
