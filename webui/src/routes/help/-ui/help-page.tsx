import { memo, useCallback, useEffect, useRef, useState } from 'react';

import { buildDebugInfoText } from '../-help.debug-info';
import { consumePendingDocsSection, DOCS_NAVIGATE_EVENT } from '../-help.navigate';
import { DOCS_SECTIONS } from '../-help.sections';

/**
 * The Help/Docs page: one long scrollable document. Every `.docs-section` is
 * rendered up front (the legacy behaviour the characterization spec pins), the
 * sidebar nav scrolls to anchors, and the filter box hides non-matching
 * sections rather than re-querying anything.
 *
 * Ids and class names are the legacy ones on purpose — style.css owns the
 * look, and the spec in `webui/tests/pages/help.spec.ts` keys on them.
 */

interface NavActive {
  section: string | null;
  child: string | null;
}

const FIRST = DOCS_SECTIONS[0];

export function HelpPage() {
  const contentRef = useRef<HTMLElement | null>(null);
  const navRef = useRef<HTMLDivElement | null>(null);
  // Suppress the scroll spy during click-initiated scrolls, exactly like the
  // legacy 800ms window — otherwise the smooth scroll re-highlights every
  // section it passes on the way down.
  const spySuppressedUntil = useRef(0);

  const [active, setActive] = useState<NavActive>({
    section: FIRST?.id ?? null,
    child: FIRST?.children[0]?.id ?? null,
  });
  const [query, setQuery] = useState('');
  const [hiddenSections, setHiddenSections] = useState<ReadonlySet<string>>(new Set());
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(null);

  const suppressSpy = useCallback(() => {
    spySuppressedUntil.current = Date.now() + 800;
  }, []);

  // Scroll a target element into view within the docs-content container.
  // Manual offsetTop math instead of scrollIntoView (lazy-loaded images
  // haven't reserved height yet), with correction passes after they settle —
  // the exact legacy scrollDocTarget.
  const scrollDocTarget = useCallback(
    (targetId: string) => {
      const docsContent = contentRef.current;
      if (!docsContent || !document.getElementById(targetId)) return;
      suppressSpy();

      const calcOffset = (el: HTMLElement): number => {
        let offset = 0;
        let current: HTMLElement | null = el;
        while (current && current !== docsContent) {
          offset += current.offsetTop;
          current = current.offsetParent as HTMLElement | null;
        }
        return offset;
      };

      const place = () => {
        // Re-resolve on every pass: a React re-render can replace the
        // injected section nodes between the initial scroll and a
        // correction, and a captured node would measure as detached (0).
        const target = document.getElementById(targetId);
        if (!target) return;
        // Desktop: .docs-content is its own scroll container. The mobile
        // layout stacks the panels so the page scroller owns the document —
        // assigning scrollTop there is a silent no-op, so fall back to
        // scrollIntoView.
        if (docsContent.scrollHeight > docsContent.clientHeight + 1) {
          docsContent.scrollTop = calcOffset(target);
        } else {
          target.scrollIntoView();
        }
      };

      place();
      window.setTimeout(place, 150);
      window.setTimeout(place, 500);
    },
    [suppressSpy],
  );

  const navigateToSection = useCallback(
    (sectionId: string) => {
      // A child id activates its parent section; a section id activates its
      // first child — mirrors how the legacy deep link left the nav.
      for (const section of DOCS_SECTIONS) {
        if (section.id === sectionId) {
          setActive({ section: section.id, child: section.children[0]?.id ?? null });
          scrollDocTarget('docs-' + section.id);
          return;
        }
        if (section.children.some((c) => c.id === sectionId)) {
          setActive({ section: section.id, child: sectionId });
          scrollDocTarget(sectionId);
          return;
        }
      }
      // Unknown id (e.g. a subsection anchor inside a section's HTML): scroll
      // to it if it exists at all.
      if (document.getElementById(sectionId)) scrollDocTarget(sectionId);
    },
    [scrollDocTarget],
  );

  // The app-wide navigateToDocsSection contract: consume a pending target on
  // mount (cross-page "Learn more →"), and follow live events while mounted.
  useEffect(() => {
    const pending = consumePendingDocsSection();
    if (pending) {
      // Let the sections paint first — same tick ordering as the legacy
      // 300ms navigate delay, without the arbitrary wait.
      requestAnimationFrame(() => navigateToSection(pending));
    }
    const onNavigate = (e: Event) => {
      const sectionId = (e as CustomEvent<{ sectionId?: string }>).detail?.sectionId;
      if (sectionId) {
        consumePendingDocsSection(); // the event supersedes the stash
        navigateToSection(sectionId);
      }
    };
    window.addEventListener(DOCS_NAVIGATE_EVENT, onNavigate);
    return () => window.removeEventListener(DOCS_NAVIGATE_EVENT, onNavigate);
  }, [navigateToSection]);

  // Filter: hide sections whose rendered text doesn't contain the query.
  // Reads textContent from the DOM (matches what the user can see, exactly
  // like the legacy), then narrows both panes through state.
  useEffect(() => {
    const q = query.toLowerCase().trim();
    if (!q) {
      setHiddenSections(new Set());
      return;
    }
    const hidden = new Set<string>();
    for (const section of DOCS_SECTIONS) {
      const el = document.getElementById('docs-' + section.id);
      if (el && !(el.textContent ?? '').toLowerCase().includes(q)) hidden.add(section.id);
    }
    setHiddenSections(hidden);
  }, [query]);

  // Scroll spy — highlight the section/child currently in view.
  const onContentScroll = useCallback(() => {
    if (Date.now() < spySuppressedUntil.current) return;
    const docsContent = contentRef.current;
    if (!docsContent) return;

    const threshold = docsContent.getBoundingClientRect().top + 120;
    let activeSection: string | null = null;
    let activeChild: string | null = null;

    for (const section of DOCS_SECTIONS) {
      const el = document.getElementById('docs-' + section.id);
      if (el && el.getBoundingClientRect().top <= threshold) activeSection = section.id;
      for (const child of section.children) {
        const childEl = document.getElementById(child.id);
        if (childEl && childEl.getBoundingClientRect().top <= threshold) activeChild = child.id;
      }
    }

    if (!activeSection && DOCS_SECTIONS.length) {
      activeSection = DOCS_SECTIONS[0].id;
      activeChild = DOCS_SECTIONS[0].children[0]?.id ?? null;
    }
    setActive({ section: activeSection, child: activeChild });
  }, []);

  // Lightbox: any docs screenshot zooms on click. Delegated, exactly like the
  // legacy openDocsLightbox, because the images live inside injected HTML.
  const onContentClick = useCallback((e: React.MouseEvent<HTMLElement>) => {
    const wrapper = (e.target as HTMLElement).closest('.docs-img-wrapper');
    const img = wrapper?.querySelector('img');
    if (img?.src) setLightbox({ src: img.src, alt: img.alt });
  }, []);

  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setLightbox(null);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [lightbox]);

  const onSectionTitleClick = (sectionId: string) => {
    // Clicking the expanded section collapses everything (legacy behaviour);
    // clicking a collapsed one expands it and scrolls to it.
    if (active.section === sectionId) {
      setActive({ section: null, child: null });
    } else {
      setActive({ section: sectionId, child: null });
    }
    scrollDocTarget('docs-' + sectionId);
  };

  const onChildClick = (sectionId: string, childId: string) => {
    setActive({ section: sectionId, child: childId });
    scrollDocTarget(childId);
  };

  return (
    <div className="docs-layout">
      <nav className="docs-sidebar" id="docs-sidebar">
        <div className="docs-sidebar-header">
          <h3>Documentation</h3>
          <input
            type="text"
            className="docs-search"
            id="docs-search-input"
            placeholder="Filter docs…"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <DebugInfoPanel />
        </div>
        <div className="docs-nav" id="docs-nav" ref={navRef}>
          {DOCS_SECTIONS.map((section) => (
            <div
              key={section.id}
              className="docs-nav-section"
              data-section={section.id}
              style={hiddenSections.has(section.id) ? { display: 'none' } : undefined}
            >
              <div
                className={`docs-nav-section-title${active.section === section.id ? ' expanded active' : ''}`}
                data-target={section.id}
                onClick={() => onSectionTitleClick(section.id)}
              >
                <img
                  className="docs-nav-icon"
                  src={section.icon}
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
                <span>{section.title}</span>
                <span className="docs-nav-arrow">▶</span>
              </div>
              {section.children.length > 0 && (
                <div
                  className={`docs-nav-children${active.section === section.id ? ' expanded' : ''}`}
                  data-parent={section.id}
                >
                  {section.children.map((child) => (
                    <div
                      key={child.id}
                      className={`docs-nav-child${active.child === child.id ? ' active' : ''}`}
                      data-target={child.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        onChildClick(section.id, child.id);
                      }}
                    >
                      {child.title}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </nav>
      <main
        className="docs-content"
        id="docs-content"
        ref={contentRef}
        onScroll={onContentScroll}
        onClick={onContentClick}
      >
        <DocsContent hidden={hiddenSections} />
      </main>
      {lightbox && (
        <div className="docs-lightbox" onClick={() => setLightbox(null)}>
          <button className="docs-lightbox-close">×</button>
          <img src={lightbox.src} alt={lightbox.alt} />
        </div>
      )}
    </div>
  );
}

// The document itself, isolated behind memo so nav-state churn (clicks,
// scroll-spy) can never re-render it: re-applying ~100KB of injected HTML
// would replace the section nodes mid-scroll (detaching in-flight scroll
// targets) and burn layout work on every spy tick. Only the filter's hidden
// set re-renders this — and content() runs exactly once per section.
const sectionHtml = new Map<string, string>();

const DocsContent = memo(function DocsContent({ hidden }: { hidden: ReadonlySet<string> }) {
  if (sectionHtml.size === 0) {
    for (const section of DOCS_SECTIONS) sectionHtml.set(section.id, section.content());
  }
  return (
    <>
      {DOCS_SECTIONS.map((section) => (
        <div
          key={section.id}
          className="docs-section"
          id={`docs-${section.id}`}
          style={hidden.has(section.id) ? { display: 'none' } : undefined}
        >
          <h2 className="docs-section-title">
            <img
              className="docs-section-icon"
              src={section.icon}
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = 'none';
              }}
            />
            <span>{section.title}</span>
          </h2>
          {/* Trusted, in-repo authored documentation HTML — the same strings
              the legacy page assigned via innerHTML. */}
          <div dangerouslySetInnerHTML={{ __html: sectionHtml.get(section.id) ?? '' }} />
        </div>
      ))}
    </>
  );
});

/**
 * The "Copy Debug Info" affordance in the sidebar header: fetch
 * /api/debug-info, build the plain-text report, copy it — with the
 * execCommand fallback for HTTP-over-LAN, and a select-all modal when both
 * clipboard paths are blocked. Same ladder as the legacy handler.
 */
function DebugInfoPanel() {
  const [logLines, setLogLines] = useState('100');
  const [logSource, setLogSource] = useState('app');
  const [buttonState, setButtonState] = useState<'idle' | 'collecting' | 'copied' | 'failed'>(
    'idle',
  );
  const [modalText, setModalText] = useState<string | null>(null);
  const resetTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(resetTimer.current), []);

  const collect = async () => {
    try {
      setButtonState('collecting');
      const resp = await fetch(`/api/debug-info?lines=${logLines}&log=${logSource}`);
      const data = await resp.json();
      const text = buildDebugInfoText(data);

      // navigator.clipboard requires HTTPS/localhost; fall back to
      // execCommand for Docker/LAN HTTP access.
      let copied = false;
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(text);
          copied = true;
        } catch {
          /* fall through to execCommand */
        }
      }
      if (!copied) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px';
        document.body.appendChild(ta);
        ta.select();
        try {
          copied = document.execCommand('copy');
        } catch {
          /* clipboard blocked entirely */
        }
        document.body.removeChild(ta);
      }

      if (copied) {
        setButtonState('copied');
        resetTimer.current = window.setTimeout(() => setButtonState('idle'), 2000);
      } else {
        setModalText(text);
        setButtonState('idle');
      }
    } catch (err) {
      console.error('Debug info error:', err);
      setButtonState('failed');
      resetTimer.current = window.setTimeout(() => setButtonState('idle'), 2000);
    }
  };

  const label =
    buttonState === 'collecting'
      ? 'Collecting...'
      : buttonState === 'copied'
        ? '✅ Copied!'
        : buttonState === 'failed'
          ? '❌ Failed'
          : '📋 Copy Debug Info';

  return (
    <div className="docs-debug-wrap">
      <button
        className={`docs-debug-button${buttonState === 'copied' ? ' copied' : ''}`}
        onClick={() => void collect()}
      >
        {label}
      </button>
      <div className="docs-debug-options">
        <div className="docs-debug-row">
          <label>Log lines</label>
          <select
            className="docs-debug-select"
            id="debug-log-lines"
            value={logLines}
            onChange={(e) => setLogLines(e.target.value)}
          >
            <option value="20">20</option>
            <option value="50">50</option>
            <option value="100">100</option>
            <option value="200">200</option>
            <option value="500">500</option>
          </select>
        </div>
        <div className="docs-debug-row">
          <label>Log source</label>
          <select
            className="docs-debug-select"
            id="debug-log-source"
            value={logSource}
            onChange={(e) => setLogSource(e.target.value)}
          >
            <option value="app">app.log</option>
            <option value="post_processing">post_processing.log</option>
            <option value="acoustid">acoustid.log</option>
            <option value="source_reuse">source_reuse.log</option>
          </select>
        </div>
      </div>
      {modalText !== null && (
        <div
          id="debug-text-modal"
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            background: 'rgba(0,0,0,0.7)',
            zIndex: 10000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) setModalText(null);
          }}
        >
          <div
            style={{
              background: '#1a1a2e',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: 12,
              padding: 20,
              width: '90%',
              maxWidth: 700,
              maxHeight: '80vh',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ color: '#fff', fontWeight: 600 }}>
                Debug Info — Select All &amp; Copy
              </span>
              <button
                style={{
                  background: 'none',
                  border: 'none',
                  color: '#888',
                  fontSize: 18,
                  cursor: 'pointer',
                }}
                onClick={() => setModalText(null)}
              >
                ✕
              </button>
            </div>
            <textarea
              readOnly
              value={modalText}
              ref={(ta) => {
                ta?.focus();
                ta?.select();
              }}
              style={{
                width: '100%',
                height: '50vh',
                background: '#0d0d1a',
                color: '#e0e0e0',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 8,
                padding: 12,
                fontFamily: 'monospace',
                fontSize: 12,
                resize: 'none',
                outline: 'none',
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
