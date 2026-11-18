/**
 * What's new (plan 4.7, mockup's whatsnew view): the running release's
 * header, what is new since you last looked in this browser, every item
 * with Show me where it has somewhere to go, the earlier releases in a line,
 * and the full notes behind the version button. All from release-notes.ts,
 * the same file the version modal reads.
 *
 * "Since you last looked" is soulsync_helper_version_seen, the key the old
 * helper and the version modal use, read once when the app loads: Home's
 * tile marks the version seen on the way in, so the view can't read it then.
 */

import { useEffect, useState } from 'react';

import type { ViewProps } from './view-props';

import { markVersionSeen, readSeenVersion } from './home';
import { Icon } from './icons';
import { openReleaseNotes } from './links';
import {
  buildVersionFromPage,
  compareVersions,
  currentRelease,
  type ReleaseItem,
} from './release-notes';
import { useHelpStore } from './store';
import { isAnchorId } from './tours/anchors';
import { showMe } from './tours/index';
import { findAnchor, goTo, pageAllowed } from './tours/navigate';
import { Button, Label, Placeholder } from './ui';
import w from './whats-new.module.css';

/** the version this browser had seen when the app loaded, then whatever was read since */
let seenSnapshot: string | null = readSeenVersion();

/** for tests: what the app "loaded with" */
export function resetSeenSnapshot(value: string | null = readSeenVersion()): void {
  seenSnapshot = value;
}

/** Show me: a spotlight on the thing, or its page, or nothing when neither is reachable */
export function showMeAction(item: ReleaseItem): (() => void) | null {
  const { anchor, page } = item;
  if (isAnchorId(anchor) && (findAnchor(anchor) || (page && pageAllowed(page)))) {
    return () => {
      useHelpStore.getState().close();
      showMe({ anchor, page, title: item.title, body: item.desc });
    };
  }
  if (page && pageAllowed(page)) {
    // the sheet stays beside the page on desktop, so the notes are still there
    return () => goTo(page);
  }
  return null;
}

export function WhatsNewView(_: ViewProps) {
  const serverVersion = useHelpStore((st) => st.update?.current_version ?? null);
  const build = serverVersion || buildVersionFromPage();
  const release = currentRelease(build);
  const [seenBefore] = useState(() => seenSnapshot);

  useEffect(() => {
    const now = build || release?.version;
    markVersionSeen(now);
    return () => {
      if (now) seenSnapshot = now;
    };
  }, [build, release?.version]);

  if (!release) {
    return (
      <Placeholder title="What's new" text="The notes for this version aren't here.">
        <Button kind="primary" icon="spark" onClick={openReleaseNotes}>
          Full release notes
        </Button>
      </Placeholder>
    );
  }

  const fresh = !seenBefore || compareVersions(seenBefore, release.version) < 0;
  const count = release.items.length;
  const things = count === 1 ? '1 thing' : `${count} things`;

  return (
    <>
      <section className={w.head} aria-labelledby="whats-new-headline">
        <p className={w.version}>
          {release.version} · {release.month}
        </p>
        <h3 className={w.headline} id="whats-new-headline">
          {release.headline}
        </h3>
        <p className={w.lede}>
          {fresh
            ? `New since you last looked: ${things}. The full notes are on the version button.`
            : `You have seen everything in ${release.version}. The full notes are on the version button.`}
        </p>
      </section>

      <Label>{fresh ? 'New for you' : `In ${release.version}`}</Label>
      <ul className={w.items}>
        {release.items.map((item) => (
          <Item key={item.title} item={item} fresh={fresh} />
        ))}
      </ul>

      {release.earlier ? (
        <>
          <Label>Earlier</Label>
          <ul className={w.items}>
            <li className={w.item} data-earlier>
              <p className={w.itemDesc}>{release.earlier.desc}</p>
            </li>
          </ul>
        </>
      ) : null}

      <div className={w.more}>
        <Button
          icon="spark"
          onClick={() => {
            useHelpStore.getState().close();
            openReleaseNotes();
          }}
        >
          Full release notes
        </Button>
      </div>
    </>
  );
}

function Item({ item, fresh }: { item: ReleaseItem; fresh: boolean }) {
  const action = showMeAction(item);
  return (
    <li className={w.item} data-new={fresh || undefined}>
      <h4 className={w.itemTitle}>{item.title}</h4>
      <p className={w.itemDesc}>{item.desc}</p>
      {action ? (
        <button
          type="button"
          className={w.showMe}
          onClick={action}
          aria-label={`Show me: ${item.title}`}
        >
          Show me
          <Icon name="chev" size={13} />
        </button>
      ) : null}
    </li>
  );
}
