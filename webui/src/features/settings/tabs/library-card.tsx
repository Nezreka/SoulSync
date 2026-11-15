/**
 * What the Library tab's two media cards share (Folders, step 8a; Organization,
 * step 8b): the collapsible section, the Music / Audiobooks / Podcasts / Video
 * tabs, and the vanilla panels parked in #library-parked-panels and placed
 * back between React's own while the card is mounted.
 */

import {
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  useEffect,
  useRef,
  useState,
} from 'react';

export const LIBRARY_TAB = 'library';

export type Media = 'music' | 'audiobooks' | 'podcasts' | 'video';

const MEDIA: Array<[Media, string]> = [
  ['music', 'Music'],
  ['audiobooks', 'Audiobooks'],
  ['podcasts', 'Podcasts'],
  ['video', 'Video'],
];

/** a Library section: header and body, the body a media card */
export function LibrarySection({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: ReactNode;
}) {
  return (
    <>
      <div
        className="settings-section-header collapsed"
        data-stg={LIBRARY_TAB}
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
        <h3>{title}</h3>
        <span className="settings-section-hint">{hint}</span>
      </div>
      <div
        className="settings-section-body collapsed stg-media-card"
        data-stg={LIBRARY_TAB}
        style={{ display: 'none' }}
      >
        {children}
      </div>
    </>
  );
}

/**
 * The card's tabs (`<prefix>-<media>-tab`, controlling `<prefix>-<media>-panel`),
 * click and arrow keys as handleLibraryMediaTabKey did. The settings search
 * still switches a card by calling switchLibraryMediaTab on a tab, which clicks
 * a React one (settings.js).
 */
export function useMediaTabs(prefix: string, label: string) {
  const [media, setMedia] = useState<Media>('music');
  const refs = useRef<Partial<Record<Media, HTMLButtonElement | null>>>({});
  const onKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const current = MEDIA.findIndex(([k]) => k === media);
    const index =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? MEDIA.length - 1
          : (current + (event.key === 'ArrowRight' ? 1 : -1) + MEDIA.length) % MEDIA.length;
    event.preventDefault();
    setMedia(MEDIA[index][0]);
    refs.current[MEDIA[index][0]]?.focus();
  };
  const tabs = (
    <div className="stg-media-switcher" role="tablist" aria-label={label}>
      {MEDIA.map(([key, text]) => (
        <button
          key={key}
          ref={(el) => {
            refs.current[key] = el;
          }}
          type="button"
          id={`${prefix}-${key}-tab`}
          role="tab"
          aria-selected={media === key ? 'true' : 'false'}
          aria-controls={`${prefix}-${key}-panel`}
          tabIndex={media === key ? 0 : -1}
          onClick={() => setMedia(key)}
          onKeyDown={onKey}
        >
          {text}
        </button>
      ))}
    </div>
  );
  return { media, tabs };
}

/**
 * The card's vanilla panels: moved in after the React panel each followed, in
 * their old order, and back to #library-parked-panels when the card goes. The
 * tabs show and hide them like the rest.
 */
export function useParkedPanels(
  media: Media,
  places: Array<{ id: string; after: RefObject<HTMLElement | null>; media: Media }>,
) {
  useEffect(() => {
    const nodes = places.map(({ id, after }) => {
      const node = document.getElementById(id);
      if (node && after.current) after.current.after(node);
      return node;
    });
    return () => {
      const park = document.getElementById('library-parked-panels');
      if (park) for (const node of nodes) if (node) park.appendChild(node);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    for (const place of places) {
      const node = document.getElementById(place.id);
      if (node) node.hidden = media !== place.media;
    }
  }, [media]); // eslint-disable-line react-hooks/exhaustive-deps
}
