import { QueryClientContext } from '@tanstack/react-query';
import { useContext, useState } from 'react';

import type { Explanation } from '../-discover.explanation';

import { type FeedbackAction, type FeedbackEntity, postDiscoverFeedback } from '../-discover.api';
import { ActionMenu, type ActionMenuItem } from '../../artist-detail/-ui/action-menu';

/**
 * The ⋯ on a recommendation: save for later, more like this, less like this,
 * not now, block.
 *
 * The answer is stored with the explanation the item was shown with, which is
 * how "less like this" knows which seed brought it. More and less re-rank the
 * artist shelves; not now and block hide the item everywhere (the server
 * filters every discover surface), so the card goes at once instead of
 * waiting for the refetch.
 */

export const NOT_NOW_DAYS = 30;

export function feedbackToast(action: FeedbackAction, entity: FeedbackEntity): string {
  const artist = entity.type === 'artist' ? entity.name : entity.artist_name || entity.name;
  switch (action) {
    case 'save':
      return 'Saved to your inbox';
    case 'more':
      return `You'll see more like ${entity.name}`;
    case 'less':
      return `You'll see less like ${entity.name}`;
    case 'not_now':
      return `Hidden for ${NOT_NOW_DAYS} days`;
    case 'block':
      return `Blocked ${artist}`;
  }
}

export function feedbackItems(
  entity: FeedbackEntity,
  answer: (action: FeedbackAction) => void,
): ActionMenuItem[] {
  const artist = entity.type === 'artist' ? entity.name : entity.artist_name || entity.name;
  return [
    {
      key: 'save',
      label: 'Save for later',
      title: 'Keep it in your inbox',
      className: 'discover-feedback-save',
      onSelect: () => answer('save'),
    },
    {
      key: 'more',
      label: 'More like this',
      className: 'discover-feedback-more',
      onSelect: () => answer('more'),
    },
    {
      key: 'less',
      label: 'Less like this',
      className: 'discover-feedback-less',
      onSelect: () => answer('less'),
    },
    {
      key: 'not_now',
      label: 'Not now',
      title: `Hide it for ${NOT_NOW_DAYS} days`,
      className: 'discover-feedback-not-now',
      onSelect: () => answer('not_now'),
    },
    {
      key: 'block',
      label: `Block ${artist}`,
      title: 'Never recommend or download this artist',
      className: 'discover-feedback-block',
      danger: true,
      onSelect: () => answer('block'),
    },
  ];
}

export function FeedbackMenu({
  entity,
  explanation,
  onHidden,
  className,
}: {
  entity: FeedbackEntity;
  explanation?: Explanation;
  /** Not now or block landed: the caller drops the card. */
  onHidden?: () => void;
  className?: string;
}) {
  // optional: the menu still records an answer where no query client is mounted
  const queryClient = useContext(QueryClientContext);
  const [busy, setBusy] = useState(false);

  const answer = (action: FeedbackAction) => {
    if (busy) return;
    setBusy(true);
    postDiscoverFeedback({ action, entity, explanation })
      .then((res) => {
        if (res.success === false) throw new Error(res.error || 'not saved');
        window.showToast?.(feedbackToast(action, entity), 'success');
        if (action === 'not_now' || action === 'block') onHidden?.();
        // hiding can touch any shelf; more and less re-rank the artist ones;
        // a save lands in the inbox, whose nav badge polls on its own timer
        if (action === 'save') window.refreshDiscoverInboxBadge?.();
        void queryClient?.invalidateQueries({ queryKey: ['discover'] });
      })
      .catch(() => {
        window.showToast?.("Couldn't save that. Try again in a moment.", 'error');
      })
      .finally(() => setBusy(false));
  };

  return (
    <ActionMenu
      items={feedbackItems(entity, answer)}
      className="discover-feedback-menu"
      trigger={({ onClick, ...props }) => (
        <button
          type="button"
          {...props}
          className={`discover-feedback-trigger${className ? ` ${className}` : ''}`}
          aria-label={`Tell discovery about ${entity.name}`}
          title="More like this, less like this, not now, block"
          disabled={busy}
          onClick={(e) => {
            // the cards are links: the menu opens, the card does not navigate
            e.preventDefault();
            onClick(e);
          }}
        >
          ⋯
        </button>
      )}
    />
  );
}
