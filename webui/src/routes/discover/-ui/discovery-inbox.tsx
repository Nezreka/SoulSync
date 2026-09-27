import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { browserSafeImageUrl } from '@/platform/artwork-thumb';

import type { InboxItem, InboxView } from '../-discover.inbox';
import type { RecentAlbum } from '../-discover.recent-releases';

import { dismissAllInbox, fetchInbox, setInboxState } from '../-discover.api';
import {
  INBOX_EMPTY,
  INBOX_PREVIEW,
  inboxKindLabel,
  inboxReleaseAlbum,
  inboxWhen,
  isRelease,
  unansweredLine,
} from '../-discover.inbox';

/**
 * The inbox on the Discover page: what's new since you looked (releases from
 * artists you watch, what's coming out, concerts) and what you saved for
 * later. Save and dismiss are the triage; opening a release is the same
 * album flow as Recent Releases. The nav badge counts what's new.
 */

const KIND_GLYPH: Record<string, string> = {
  new_release: '💿',
  upcoming: '📅',
  saved_rec: '🔖',
  concert: '🎟️',
};

function refreshBadge() {
  window.refreshDiscoverInboxBadge?.();
}

export function DiscoveryInbox({
  onOpenRelease,
  buildArtistPath,
}: {
  onOpenRelease: (album: RecentAlbum) => void;
  /** An artist page for a saved rec, or '' when there's no id to open. */
  buildArtistPath: (item: InboxItem) => string;
}) {
  const queryClient = useQueryClient();
  const [view, setView] = useState<InboxView>('new');
  const [expanded, setExpanded] = useState(false);
  const query = useQuery({
    queryKey: ['discover', 'inbox', view] as const,
    queryFn: () => fetchInbox(view),
    // a background refresh is running: look again shortly for what it found
    refetchInterval: (q) => (q.state.data?.refreshing ? 4000 : false),
    retry: false,
  });

  const settle = () => {
    void queryClient.invalidateQueries({ queryKey: ['discover', 'inbox'] });
    refreshBadge();
  };

  const move = (item: InboxItem, state: 'saved' | 'dismissed') => {
    void setInboxState(item.id, state)
      .then(settle)
      .catch(() => window.showToast?.("Couldn't update the inbox. Try again.", 'error'));
  };

  const dismissAll = () => {
    void dismissAllInbox()
      .then(settle)
      .catch(() => window.showToast?.("Couldn't clear the inbox. Try again.", 'error'));
  };

  const data = query.data;
  const items = data?.items ?? [];
  const unread = data?.counts?.unread ?? 0;
  const shown = expanded ? items : items.slice(0, INBOX_PREVIEW);
  const note = query.isError
    ? "Couldn't load your inbox."
    : data?.refreshing
      ? 'Checking for anything new…'
      : unansweredLine(data?.unanswered);

  return (
    <section className="discover-inbox" id="discover-inbox" aria-labelledby="discover-inbox-title">
      <div className="discover-inbox-header">
        <h2 className="discover-inbox-title" id="discover-inbox-title">
          Inbox
          {unread ? <span className="discover-inbox-count">{unread}</span> : null}
        </h2>
        <div className="discover-inbox-tabs" role="tablist" aria-label="Inbox view">
          {(['new', 'saved'] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              className={view === v ? 'discover-inbox-tab active' : 'discover-inbox-tab'}
              onClick={() => {
                setView(v);
                setExpanded(false);
              }}
            >
              {v === 'new' ? 'New' : 'Saved'}
            </button>
          ))}
        </div>
        {view === 'new' && items.length > 0 ? (
          <button type="button" className="discover-inbox-clear" onClick={dismissAll}>
            Dismiss all
          </button>
        ) : null}
      </div>
      {note ? (
        <p className="discover-inbox-note" role="status">
          {note}
        </p>
      ) : null}
      {query.isPending ? (
        <p className="discover-inbox-empty">Loading…</p>
      ) : items.length === 0 ? (
        <p className="discover-inbox-empty">{INBOX_EMPTY[view]}</p>
      ) : (
        <ul className="discover-inbox-list">
          {shown.map((item) => (
            <InboxRow
              key={item.id}
              item={item}
              view={view}
              onOpenRelease={onOpenRelease}
              artistPath={buildArtistPath(item)}
              onMove={move}
            />
          ))}
        </ul>
      )}
      {items.length > INBOX_PREVIEW ? (
        <button
          type="button"
          className="discover-inbox-more"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? 'Show less' : `Show all ${items.length}`}
        </button>
      ) : null}
    </section>
  );
}

function InboxRow({
  item,
  view,
  artistPath,
  onOpenRelease,
  onMove,
}: {
  item: InboxItem;
  view: InboxView;
  artistPath: string;
  onOpenRelease: (album: RecentAlbum) => void;
  onMove: (item: InboxItem, state: 'saved' | 'dismissed') => void;
}) {
  const [broken, setBroken] = useState(false);
  const cover = item.image_url && !broken ? browserSafeImageUrl(item.image_url) : '';
  const ticketUrl = item.kind === 'concert' ? item.payload?.url : '';
  return (
    <li className={`discover-inbox-item discover-inbox-item--${item.kind}`}>
      <span className="discover-inbox-art" aria-hidden="true">
        {cover ? (
          <img src={cover} alt="" loading="lazy" onError={() => setBroken(true)} />
        ) : (
          (KIND_GLYPH[item.kind] ?? '✨')
        )}
      </span>
      <span className="discover-inbox-text">
        <span className="discover-inbox-kind">{inboxKindLabel(item.kind)}</span>
        <span className="discover-inbox-name" title={item.title}>
          {item.title}
        </span>
        <span className="discover-inbox-sub">
          {item.kind === 'saved_rec' && item.payload?.entity_type === 'artist'
            ? inboxWhen(item)
            : [item.artist_name, inboxWhen(item)].filter(Boolean).join(' · ')}
        </span>
      </span>
      <span className="discover-inbox-actions">
        {isRelease(item) ? (
          <button
            type="button"
            className="btn btn--sm btn--secondary"
            onClick={() => onOpenRelease(inboxReleaseAlbum(item))}
          >
            Open
          </button>
        ) : ticketUrl ? (
          <a
            className="btn btn--sm btn--secondary"
            href={ticketUrl}
            target="_blank"
            rel="noreferrer"
          >
            Tickets
          </a>
        ) : artistPath ? (
          <a className="btn btn--sm btn--secondary" href={artistPath}>
            Artist
          </a>
        ) : null}
        {view === 'new' ? (
          <button
            type="button"
            className="btn btn--sm btn--secondary"
            aria-label={`Save ${item.title}`}
            onClick={() => onMove(item, 'saved')}
          >
            Save
          </button>
        ) : null}
        <button
          type="button"
          className="discover-inbox-dismiss"
          aria-label={view === 'saved' ? `Remove ${item.title}` : `Dismiss ${item.title}`}
          title={view === 'saved' ? 'Remove' : 'Dismiss'}
          onClick={() => onMove(item, 'dismissed')}
        >
          ✕
        </button>
      </span>
    </li>
  );
}
