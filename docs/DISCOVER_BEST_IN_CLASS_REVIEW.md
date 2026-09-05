# SoulSync Discover: critical visual and UX review and implementation specification

**Review date:** 2026-09-04  
**Repository baseline:** `0cdace396`  
**Reviewed surfaces:** Music `/discover`; Video `/video-discover`; their immediate browsing, mix, filter, and preference interactions.  
**Intended implementer:** Claude, working in this repository.  
**Status:** Review and proposed requirements. Tasks below are not implemented by this document.

> **Implementation progress (updated 2026-09-04 by Claude).** Batch 1 only.
> The checklists in §3.1, §12 and §13 are marked below; `[x]` means done and
> verified, `[~]` means the part scoped into batch 1 is done and the rest is
> named. Per-task detail — what was reproduced, what was implemented, what was
> deliberately left for later batches, and what is still unverified — is in
> [DISCOVER_BIC_PROGRESS.md](DISCOVER_BIC_PROGRESS.md). Nothing outside batch 1
> has been started.

## 1. Verdict

**Neither discover page is best in class today.** The product has unusually useful raw ingredients: music mixes, playable-library resolution, artist radio, collection gaps, graph exploration, video acquisition, streaming-service filtering, and independent source integrations. The problem is converting those ingredients into a confident, attractive, reliable decision for a person.

Music currently feels like a collection-management dashboard with listening attached. Its largest visual emphasis is an artist you can monitor, a panel of navigation shortcuts, and counts of “signals,” “leads,” and “tools.” Its actual listening entry points arrive later. A play glyph on a mix opens a dialog; a preview glyph inside that dialog has an empty handler. The distinction between enjoying something, saving something, and instructing automation to obtain something is insufficiently clear.

Video is closer to a polished entertainment surface: the artwork, wordmarks, and poster presentation are materially stronger. But the hero is enormous, an owned title offers “More info” instead of an immediate watching decision, and a large genre directory precedes personal recommendations. Much of the experience is a long catalog presented as many shelves. Presentation quality is undermined by mobile control collisions, weak state restoration, misleading labels, and optimistic preference actions that do not reliably communicate failure.

Beating the named competitors requires two qualities at once: the clarity and pleasure of a media service, and the operational trust of a capable media manager. More gradients, larger posters, additional shelves, or an even bigger hero will not achieve that. The differentiator should be: **find something you actually want; know exactly whether you can enjoy it now; save or acquire it with predictable consequences; return without losing your place.**

### 1.1 What this review does and does not establish

- Source inspection covered the active React music page, the legacy video discover script, shared discovery CSS, relevant music hooks/components, video API routes, and recommendation/provider helpers.
- Actual local UI was inspected in authenticated headless Microsoft Edge through the repository's installed Playwright tooling. Desktop: 1440×1000. Mobile: 390×844, including fresh touch/mobile contexts and a desktop-to-mobile resize. Reduced-motion preference was enabled for captures.
- Music mix opening, Escape dismissal, preview click, section rendering, video shelf-to-category navigation, Browse opening, preference opening, and Browse reload behavior were checked.
- No media acquisition, watchlist bulk submission, destructive action, or provider-setting change was intentionally submitted as part of the audit. End-to-end playback, import, and external-player success were not verified. Those are explicit implementation acceptance tasks below.
- Source-confirmed risks are labeled separately from runtime observations. A risk is not automatically a reproduced production incident.
- This is an expert review of SoulSync against a stated quality bar, not a measured usability study or a claim to have tested every contemporary Netflix, Disney+, Prime Video, Apple, Sonarr, and Radarr interface.
- Content and counts varied across sessions. Captured titles and counts are examples of this installation at review time, not stable fixtures or universal recommendations.
- The PIN used for the authorized local session is intentionally excluded from this document and evidence.

### 1.2 Important corrections from verification

Do not implement fixes for these false diagnoses:

1. **Music's off-screen shelves are not missing.** Initial text extraction omitted some content because `.discover-section` uses `content-visibility: auto`. Scrolling and DOM inspection confirmed albums, eras, and other shelves exist. Preserve the performance benefit while making loading and scroll restoration reliable.
2. **The sidebar is not permanently open on every mobile load.** It covered the page after resizing an existing desktop context; fresh mobile contexts placed it off-screen at x=-280. Treat breakpoint transitions as the defect and test both entry paths.
3. **Video already supports provider regions in the backend.** `enrichment/engine.py` reads `watch_region`; the client sends `watch_region` and `flatrate` for provider filtering. The task is to surface and consistently apply that context, not invent an unsupported claim that all provider filtering ignores region.
4. **Video already has lazy shelves, independent personalization loaders, reduced-motion protection for its rotation timer, and server-directed pagination.** Keep those capabilities. Fix their gaps instead of replacing them with a less capable implementation.
5. **Music already has playback resolution and stations.** The problem is prominence, semantics, action completion, and the connection between availability and the UI; do not replace the player with an unrelated second system.

## 2. Evidence and implementation map

All source paths below are relative to the repository root. Use symbol names as the durable anchor; line numbers can move while Claude works.

### 2.1 Screenshot evidence

| Evidence | What it supports |
|---|---|
| [Music desktop](discover-review-evidence/music-desktop.jpg) | Duplicate artist imagery, dense command panel, hero action/dot collisions, first mix rail position |
| [Music fresh mobile](discover-review-evidence/discover-mobile-fresh.jpg) | Hero controls overlap; command panel occupies the remainder of the opening screen |
| [Music resized mobile](discover-review-evidence/music-mobile.jpg) | Sidebar obstruction on desktop-to-mobile resize; distinguish from fresh mobile |
| [Music deeper shelves](discover-review-evidence/music-gaps.jpg) | Real album shelves exist; artwork appears unresolved immediately after scrolling; tiny titles over dark image areas |
| [Music settled shelves](discover-review-evidence/music-gaps-settled.jpg) | Follow-up artwork/loading evidence after allowing visible images more time |
| [Music mix dialog](discover-review-evidence/music-mix.jpg) | Sync dominates Play; dense selection-first track presentation; long-title truncation |
| [Video desktop](discover-review-evidence/video-desktop.jpg) | Strong artwork, oversized hero, owned-title CTA weakness, genre directory preceding shelves |
| [Video fresh mobile](discover-review-evidence/video-discover-mobile-fresh.jpg) | Stretched/overlapping carousel indicators over Trailer; genre tiles consume the next screen |
| [Video resized mobile](discover-review-evidence/video-mobile.jpg) | Same breakpoint-transition sidebar obstruction |
| [Video shelves](discover-review-evidence/video-shelves.jpg) | Poster-rail presentation and shelf density |
| [Video category](discover-review-evidence/video-category.jpg) | Category grid presentation |
| [Video Browse](discover-review-evidence/video-browse.jpg) | Many low-contrast filter chips; ambiguous Preview and NEW badges; six-column poster grid |
| [Video preferences](discover-review-evidence/video-preferences.jpg) | Discover-wide preference presentation |
| [Interaction audit](discover-review-evidence/interaction-audit.json) | DOM section counts; Escape failure; unchanged category URL; fresh-mobile/sidebar geometry |
| [Follow-up audit](discover-review-evidence/followup-audit.json) | Artwork status, preview click observations, Browse reload, preference copy |

Screenshots are evidence of the old experience, not design targets. Initial PNG/text captures also exist in the evidence folder; the selected JPEG views above are easier to inspect. Do not publish this evidence outside the project without considering the user's library and profile information it contains.

### 2.2 Source ownership

| Area | Files / symbols to inspect and change |
|---|---|
| Music composition | `webui/src/routes/discover/-ui/discover-page.tsx`: `DiscoverPage`, `DiscoveryCommandPanel`, `DiscoveryZone`, `discoveryInsight`, `renderZoneSections` |
| Music layout policy | `-discover.layout.ts`, `-discover.section-state.ts`, `-discover.use-page.ts` |
| Music hero | `-ui/discover-hero.tsx`, `-discover.hero.ts`, `-discover.use-hero.ts` |
| Music cards and mixes | `-ui/mix-shelf.tsx`, `-ui/mix-modal.tsx`, `-discover.mixes.ts`, `-discover.use-mix-modal.ts`, `-discover.playable.ts` |
| Music albums / tracks | `-ui/album-shelves.tsx`, `-ui/cache-shelves.tsx`, `-ui/bylt-sections.tsx`, `-discover.use-album-open.ts` |
| Music taste and stations | `-ui/adventurousness-dial.tsx`, `-discover.use-recommended.ts`, `-ui/stations-row.tsx` |
| Music tools | `-ui/genre-dive-modal.tsx`, artist map/web assemblies, playlist builder and source modals |
| Music API | `-discover.api.ts`, `api/discover.py`, `api/discover_routes.py`, relevant personalized generators |
| Music styles | `webui/static/style.css`, especially `.discover-command-*`, `.discovery-zone-*`, hero, cards, mix modal and `.discover-section` |
| Video composition/state | `webui/static/video/video-discover.js`: `buildSections`, `card`, `renderHero`, `paintHeroBody`, `loadGrid`, `cachedFetch`, `wire` |
| Video markup/styles | `webui/index.html` video-discover subpage; `webui/static/video/video-side.css` `.vdsc-*`, `.vsr-*` |
| Video actions | `video-get-modal.js`, `video-wish-state.js`, `video-watchlist*.js`, `video-detail.js`; reuse existing action contracts |
| Video API/ranking | `api/video/discover.py`, `core/video/discovery_recs.py`, `core/video/discovery_gaps.py` |
| Video provider/metadata | `core/video/enrichment/engine.py`, `core/video/enrichment/clients.py` |
| Shared navigation/profile | `webui/static/init.js`, shell bridge and `webui/src/platform/shell/`; inspect actual profile/cache lifecycle before changing it |

Some comments describe old migration obligations that current code has since superseded. For example, page comments still describe `buildLayoutRows`, while the current page renders explicit zones through `renderZoneSections`. **Read executable code and inspect the DOM; do not treat migration comments as the current product specification.**

## 3. Priority and release policy

Use these priorities consistently:

- **P0 — immediate correctness/access blocker:** a core exposed action does nothing, a control cannot be operated, or layout prevents reliable activation.
- **P1 — required for the next quality release:** fundamental hierarchy, truthful state, navigation restoration, accessibility, and recovery.
- **P2 — differentiation:** stronger recommendations, tailored decision workflows, and advanced discovery.
- **P3 — optional refinement:** advanced editorial features after core success is measured.

Do not interpret P0 as “the entire application is down.” It means a blocker for shipping the discover experience at the requested quality bar. No speculative security emergency is asserted here.

### 3.1 Immediate release blockers

| ID | Surface | Finding | Evidence | Required outcome | Status |
|---|---|---|---|---|---|
| M01 | Music | Hero buttons, Watch All, View Recommended and dots collide | Desktop and fresh-mobile screenshots | Independent, non-overlapping action and indicator rows | **[x]** verified live at 1440/1024/390 and offline at 5 widths |
| M02 | Music | Track Preview has an empty page callback | `onPreviewTrack={() => {}}`; click follow-up | Every visible preview performs a defined action or explains why unavailable | **[x]** row Play wired and named; five distinguishable outcomes |
| M03 | Music | Mix dialog does not close with Escape | Runtime `escapeClosed: false`; no dialog semantics in component | Accessible modal behavior and restored focus | **[x]** shared `useAccessibleModal`; verified live |
| M04 | Music | Mix/album cards are clickable divs | `DiscoverMixCard`, `DiscoverAlbumCard` | Native keyboard-operable navigation/action targets | **[x]** for mix and album cards; artist/track/station cards untouched (batch 3) |
| M05 | Music | Adventurousness is mouse-driven without native slider semantics | `AdventurousnessDial` mouse handlers | Touch, keyboard, screen reader, and reduced-motion support | **[~]** native range + reduced motion done; the copy rename is batch 3 |
| V01 | Video | Mobile dots stretch and overlap Trailer | Fresh-mobile screenshot; `.vdsc-dot` styles | Compact indicators in reserved space with adequate hit targets | **[x]** reserved strip, 40px targets; verified live |
| V02 | Video | Old category responses can append into a newer grid | `loadGrid` captures old `c`, writes shared grid without generation check | Latest query owns rendering; stale responses discarded | **[x]** generation guard; error state split from empty |
| V03 | Video | Hide/not-interested removes a card regardless of save outcome | `wireNotInterested` | Acknowledged save or optimistic rollback, Undo, canonical state | **[x]** all occurrences, rollback, Undo; the scope question is batch 5 |
| S01 | Both | Desktop-to-mobile sidebar state obstructs content | Resized vs fresh mobile evidence | Correct breakpoint transition and recoverable menu state | **[~]** breakpoint + Escape + focus done; orbs-over-modals and player height not |

### 3.2 Dependencies, not a wholesale rewrite

1. Fix action and layout blockers in existing code.
2. Introduce shared design primitives and explicit state contracts.
3. Recompose music and video independently around user intent.
4. Add restoration, cache invalidation, and failure handling.
5. Improve ranking and decision tools once event/availability semantics are trustworthy.
6. Validate actual user tasks. Do not stop when screenshots look better.

Music and video use different runtimes today. A full video-to-React migration is **not** a prerequisite to fixing the experience. If a migration is chosen separately, preserve current working API behavior and deliver in slices. Do not combine a complete runtime migration, ranking overhaul, and visual redesign into one unreviewable patch.

## 4. The product contract both pages must satisfy

Within five seconds, a returning user should understand where they are, see personally relevant media, and know the next useful action. Within one or two deliberate activations, they should be listening/watching, inspecting a clear detail view, or saving a choice with an explicit outcome.

### 4.1 Define the three intents

| Intent | Music | Video |
|---|---|---|
| Enjoy now | Play a track, mix, album, station; continue a queue | Resume or play an available title/episode; open a named provider when appropriate |
| Discover for later | Save an album/mix; follow an artist for recommendations | Save a movie/show to a personal watch-later list |
| Grow the collection | Download missing tracks/albums; monitor artist releases | Acquire a movie/selected seasons; monitor future episodes; inspect pending work |

Saving interest must not silently mean “download everything.” Conversely, monitoring must not sound like a harmless bookmark if it triggers acquisition. Keep backend terminology where needed internally; make visible verbs describe their actual consequences.

### 4.2 Availability must be a real state, not a cosmetic ribbon

Use a normalized view model with independent fields. Do not flatten all of these into `owned: boolean`:

- Identity: stable entity key; music source ID plus canonical/recording/release identity when known; video `(kind, tmdb_id)` plus resolved library ID.
- Library presence: none, partial, complete, unknown.
- Playability: checking, playable locally, playable via named server/provider, temporarily unavailable, unsupported, unknown.
- Personal intent: saved, not saved, following/monitoring with scope, dismissed.
- Acquisition: idle, queued, searching, downloading, importing, ready, waiting, needs attention; include a job reference when applicable.
- Provenance: why recommended, source label, region, last checked time where meaningful.

A title may be both in the library and still incomplete. A mix may have 30 tracks, 18 locally playable, 12 unresolved. A movie may be on a selected streaming service without being playable inside SoulSync. Do not force these facts into one mutually exclusive badge.

### 4.3 Action hierarchy

- One visually primary action per hero, detail header, or selected-item panel.
- Playable item: primary **Play** or **Resume**; secondary **Save**; detail/overflow for acquisition or advanced options.
- Unavailable item: primary **View details** or **Get album/Get movie** depending on the actual entry context; separate **Save for later**.
- Already acquiring: primary **View progress**; no duplicate Add/Get button.
- Unknown capability: visibly checking; then an honest action. Never a disabled control with no explanation.
- A Play icon means playback. An artwork click may open details, but its icon must not promise playback if it only opens a modal.
- Toasts supplement durable state. They do not substitute for a changed card, saved flag, or progress destination.

## 5. Shared visual specification

This is a concrete starting system. Validate it against the screenshots and real content rather than treating numbers as an excuse to ignore optical quality.

### 5.1 Layout, density, and hierarchy

- Desktop content gutter: 32 px at normal widths, 24 px at smaller desktop, 16 px below 768 px. Account for the real sidebar width; do not double-apply route and component padding.
- Use one content alignment for page title, hero copy, rail heading, and first card. Avoid nested panel padding that shifts every level inward.
- Standard shelf gap: 32–40 px on desktop, 24–28 px on mobile. Heading-to-card gap: 12–16 px. Keep a two-line shelf subtitle exceptional.
- Reserve borders and filled panels for controls, dialogs, selected states, and meaningful grouping. Do not put every shelf inside a framed card.
- At 1440×900, show the page context, useful hero actions, and at least one substantive recommendation rail without scrolling. At 390×844, expose at least the beginning of that rail; no command-panel or genre-directory wall before it.
- Use content width, not viewport width alone, to choose card counts. Start with 160–184 px music cards and 160–190 px video posters on desktop; music 140–152 px in mobile rails, video 144–160 px. A mobile Browse grid should show two columns with readable labels.
- Rails should show a small portion of the next card to communicate horizontal continuation. Provide explicit desktop arrows and a See all link. Do not rely on an invisible scrollbar.
- Avoid competing vertical scroll containers except within deliberate dialogs/drawers. The page itself owns vertical browsing.

### 5.2 Typography

| Role | Desktop starting point | Mobile starting point | Rules |
|---|---|---|---|
| Page title | 28–32 px / 1.15 | 24–28 px / 1.2 | One page-level heading; not hidden below a billboard |
| Hero title | 40–52 px / 1.05 | 28–34 px / 1.1 | Use fluid scaling and long-title fallback; logo height capped separately |
| Shelf heading | 20–22 px / 1.25 | 18–20 px / 1.25 | Sentence case; eliminate duplicate eyebrow/title repetitions |
| Card title | 14–16 px / 1.3 | 14–15 px / 1.3 | Up to two lines; predictable card height |
| Supporting text | 13–14 px / 1.45 | 13–14 px / 1.45 | Essential information must remain legible |
| Utility badge | 11–12 px / 1.2 | 11–12 px / 1.2 | Optional context only; never the sole explanation of an action |

Remove the pervasive 10–11 px dashboard microcopy from the main experience. Avoid a hierarchy in which every heading is heavy bold and every explanation is nearly invisible. Use a single compatible font stack and a restrained 400/500/600/700 weight ladder.

### 5.3 Color and surfaces

- Keep SoulSync's accent identity. Use one brand accent for primary actions, with separate semantic success/warning/error tokens.
- Artwork can tint a hero atmosphere, but sampled colors must pass contrast tests before becoming a button fill or text color. Background hue derived from a title hash is decoration, not a statement about the artwork.
- Neutral dark base, subtly lifted card/overlay surfaces, and clear text contrast should carry the interface. Avoid simultaneous purple panels, green rails, multicolor genre cards, colored chip text, and bright utility orbs all competing for attention.
- Keep information readable over both almost-white and almost-black artwork using a stable scrim. Do not rely on the current image being dark enough.
- Essential normal text should meet 4.5:1 contrast; large text and meaningful control boundaries should meet their applicable contrast requirements. Measure the actual composited colors, including opacity and artwork.
- Nonselected filter text must remain readable. Current colored low-opacity labels in video Browse should become neutral readable labels; indicate selection through fill/border/checkmark and accessible state.

### 5.4 Artwork

- One hero artwork treatment per media subject. Music must not repeat the same portrait as both a giant background and a small framed foreground image unless there is a deliberate, tested composition benefit; remove the current duplication.
- Album/mix artwork uses a stable 1:1 box. Video posters use 2:3. Backdrops use a responsive crop with an explicit focal point and text-safe side.
- Set intrinsic width/height or aspect-ratio before loading. Missing art must not change card dimensions.
- Define loading, missing, and failed artwork states separately. Use a tasteful neutral placeholder with media identity, not a blank rectangle that resembles an unfinished render.
- Prefer existing thumbnail/cache utilities. Do not load full-resolution originals into 160 px tiles.
- Preload the actual first hero image. Lazy-load below-fold art with a modest lead distance; do not eager-load dozens of rails for the sake of mosaics.
- Retry failed artwork only with a bounded strategy; do not create infinite `onerror` loops.
- Capture settled art and slow-network art separately. A 3-second screenshot of a blank cover does not prove a permanently broken URL.

### 5.5 Motion and controls

- Default heroes to a stable editorial selection. If rotation remains as an option, expose Pause/Play and next/previous controls; stop for focus, hover, open dialog, document hidden, manual selection, and reduced motion.
- Put indicators in a reserved layout row. Visual dot can be 6–8 px, but its button target should be approximately 44×44 px. Make the internal dot small instead of allowing generic button min-height styles to stretch it.
- Hover lift should be restrained (2–4 px or at most a small scale increase), never cover adjacent actions or labels.
- Avoid long stagger cascades: a user should not wait for the fourteenth card to become interactive. Reduced motion removes travel/scale/auto-rotation and decorative animated waves.
- Buttons have a coherent height, radius, focus ring, disabled state, busy state, and icon spacing. Avoid mixed emoji eye/disc/star controls and unrelated icon vocabularies for equivalent actions.
- Global help/onboarding/worker bubbles must not cover card actions, modal footers, mobile navigation, or player controls. Default to one compact utility entry; expand when the user requests it.

### 5.6 Shared accessibility and responsive acceptance

- All interactive elements work by keyboard and touch; no clickable div as the only activation path.
- Distinguish links from buttons. Links navigate and support open-in-new-tab; buttons mutate or control local UI. Never nest action buttons inside a card anchor.
- Every icon-only control has a contextual accessible name, e.g. “Save Dune for later,” not 30 identical “Add” controls.
- Dialog: `role=dialog`, modal semantics, labeled title, initial focus, focus containment, Escape, inert background, scroll lock, focus restoration. Prefer the existing accessible dialog primitive if one is established; audit its actual behavior.
- Screen reader order follows visual order. Status updates use restrained live announcements; do not announce every carousel rotation or progress percent.
- Test 320, 390, 768, 1024, 1440, and 1920 CSS-pixel widths; 200% text zoom; reduced motion; touch; keyboard; browser back/forward; long names; missing artwork.
- Test resizing across the sidebar breakpoint while expanded and collapsed. On mobile, drawer state must be explicit, backdrop close must work, Escape must work, and focus must return to the menu opener. A fresh mobile pass alone is insufficient.

## 6. Music Discover: detailed implementation work

### M01 — Rebuild the opening composition and fix hero collisions [P0/P1]

**Observed:** The desktop hero occupies roughly the left two-thirds of a large opening block, with a separate command dashboard on the right. The same portrait is repeated. The Add to Watchlist pill stretches behind the carousel dots and competes with Watch All/View Recommended near the bottom. The mobile screenshot reproduces the overlap. The first mobile screen offers no actual track/mix choice.

**Change:**

1. Replace the command-grid opening with a compact page header, intent navigation, one coherent feature, and an immediately adjacent music rail.
2. Header: “Discover” with a short optional personal sentence. Actions: search music and one “Tune recommendations” entry. Do not repeat “For You” as an eyebrow, heading, card label, and command tile in the same visual neighborhood.
3. Intent navigation: **For you**, **New releases**, **Explore**, **Collection**. These should change a meaningful view or navigate to a route-backed section, not merely jump into a 7,000 px document with no return state. Define state in M10.
4. Hero on desktop: start at 300–360 px tall. Mobile: 240–300 px where content allows, growing naturally for long titles; no fixed height that clips actions. Allow a no-hero layout when there is no strong feature.
5. Hero content: a meaningful reason (“A new release from an artist you follow” / “Because you listen to [artist]”), title, artist if relevant, one sentence, primary action, secondary Save/Details. Prefer an album or playable mix feature over a generic artist with only popularity data.
6. Remove Watch All from the hero. If batch-follow is retained, place it inside an explicit recommendation selection flow with count and consequences.
7. Place controls in normal layout flow: text region, action region, then navigation controls. Do not absolutely position controls across the same bottom coordinates.
8. Remove the command panel from default For you. Replace useful shortcuts with at most three compact text links inside Explore. Eliminate “Discovery Queue,” “Next best move,” “signals,” “leads,” and “tools” from the landing view.

**Files:** `discover-page.tsx`, `discover-hero.tsx`, `-discover.use-hero.ts`, hero/command CSS.

**Acceptance:** At 390×844, 1024×768, and 1440×900, no action/dot rectangle intersects another; titles up to 80 characters fit without hiding buttons. First meaningful music card is visible on mobile. Desktop shows a useful rail. Click/tap each hero action at its center and edges. No off-screen bulk action overlaps it. A missing artist image still produces a finished composition.

### M02 — Make listening the first, truthful action [P0]

**Observed:** `DiscoverMixCard` contains a decorative `.mix-card-play` glyph but its single card handler opens the mix modal. `DiscoverPage` passes `onPreviewTrack={() => {}}` into `MixModal`. The actual preview click produced no network request; source confirms no action. `playMixNow` calls an optional `window.playTrackList` and immediately reports success without awaiting a playback outcome.

**Change:**

- Card artwork/title opens mix details using a real link or clearly labeled button. Add a separate Play button that actually calls the established play resolution path. Avoid nested controls.
- Wire row playback/preview to the existing shared player or preview capability. Before implementation, decide from current player support whether this is “Play track,” a timed preview, or a source preview. Label it accordingly; do not call full-track playback “Preview.”
- A row action must use stable track identity, not only a mutable list index. If an index is required at the boundary, resolve it against the currently rendered immutable list.
- Expose loading while resolving; prevent duplicate activation; show unavailable state next to the row with an actionable explanation.
- Check that the player bridge exists. An absent bridge is a recoverable failure, not “Playing all 50 tracks.”
- Define a player result contract: accepted/started/failed plus reason. Show “Queued” when the queue was accepted, and “Playing” only when playback actually starts. If existing bridge is fire-and-forget, add a lifecycle event/acknowledgment rather than assuming success.
- Preserve the current missing-track preload capability only with explicit UI explanation. Do not imply all rows are locally playable when some are pending remote resolution/acquisition.
- Do not start audible media during page load or hover.

**Acceptance:** Play mix reaches audible playback or an honest supported-device handoff in a test environment. Play row starts that row; opening details does not start audio. Missing bridge and failed resolution produce no success toast. Two rapid taps do not duplicate the queue. A partly playable mix describes the available portion and any pending work before/while it starts. Closing the details panel preserves playback.

### M03 — Redesign the mix detail view around listening, then selection [P0/P1]

**Observed:** In Fresh Tape, Sync is the purple primary action while Play is a small neutral control. Checkboxes and “Download selected” are permanently prominent. Track titles truncate aggressively despite a wide dialog. Escape did not close it; modal root has no dialog role.

**Change:**

1. Use the shared accessible modal/drawer foundation described in section 5.6. On mobile use a full-height sheet with a sticky compact header and safe-area-aware footer. On desktop use a generous 840–960 px maximum width if the content warrants it.
2. Header: artwork, mix title, meaningful subtitle, track count, total known duration, and availability summary. Primary Play; secondary Save playlist. Download/Sync belong in an explicit secondary menu or Collection section.
3. Default track rows: position/play indicator, artwork, title + artist, album where space allows, duration, availability/action. Desktop rows at least 52–60 px tall; mobile omit redundant album column and wrap title up to two lines.
4. Enter selection mode via “Select tracks.” Then show checkboxes, selected count, Clear, and the applicable batch action. Do not make an enjoyment flow look like a spreadsheet by default.
5. Make selection explicit by stable track IDs. Preserve it if a progress update arrives; reset or reconcile it if the mix identity changes. Explain excluded/unavailable items.
6. Selection action with zero selected is disabled with clear text; batch action shows an operation summary. After submission, show per-track results and a durable job link.
7. A loading failure offers Retry inside the dialog. Retry must retain the mix context. Empty is “No tracks in this mix yet” with a refresh/back action, not an indistinguishable error.
8. Focus the heading or primary action on opening, keep Tab inside, Escape closes, and return focus to the exact card/row opener. Background content and utility bubbles cannot receive interaction while modal.

**Acceptance:** Keyboard-only opening/closing and complete tab loop; screen reader announces title and dialog; Escape closes; 200% zoom works; long classical movement names remain identifiable; no duplicate queue/download on retries; closing during a submitted job retains job state outside the dialog.

### M04 — Establish a music card family [P0/P1]

**Observed:** Artist, album, mix, BYLT, station, and source-specific cards have different interactions and identity conventions. Album and mix cards use clickable divs. Some titles sit inside dark image gradients, and placeholders are visually near-empty.

**Change:**

- Define shared primitives with variants, not a separate bespoke action system for every shelf.
- Album: square cover, title, artist, year/type where relevant, availability indicator, detail link, separate play/save menu.
- Artist: portrait treatment, artist name, one recommendation reason, explicit Follow/Following control; do not show download semantics unless the user enters collection controls.
- Mix: square cover/mosaic, title, short description, count/duration/availability summary, direct Play, detail link.
- Track: compact row in track-focused shelves; show track title and artist before album art decoration. Do not disguise tracks as albums if click opens an album download modal.
- Station: consistent radio icon and readable “Artist radio” label, direct Play, state “Starting…” then active indicator. Explain the “With…” artists only when the station can actually deliver them through its resolution/refill rules.
- Artwork labels normally sit below art on ordinary tiles. Reserve overlaid text for editorial features where text-safe composition is controlled.
- Keep card heights aligned and expose full titles in detail/accessibility names; tooltips alone are not a mobile solution.
- Distinguish loading placeholder from unavailable cover. In the settled art audit, some first-row images were the local placeholder and others were 1000×1000 external images. Normalize this pipeline rather than calling all blank tiles a network failure.

**Acceptance:** Same item state is identical across every shelf; keyboard user can open/save/play; screen reader can distinguish all controls; cards do not shift on image failure; no hover-only critical action; 1000×1000 originals are not the default tile payload where a thumbnail is available.

### M05 — Replace the inaccessible adventurousness control [P0/P1]

**Observed:** The current wave responds to `onMouseDown`, window mousemove, and mouseup. It has no native range control, keyboard semantics, or touch pointer implementation. It also animates via React state on requestAnimationFrame, gated by `offsetParent`, which does not mean the element is in the viewport. Its description is about reducing popularity, while the endpoints say familiar artists versus deep cuts.

**Change:**

- Use a native range input or established accessible slider, with keyboard support and pointer/touch behavior. Keep a decorative wave only if it adds value; it is never the input itself.
- Rename visible concept to **Familiar ↔ Explore more** or equally clear copy. Explain the actual algorithm: if it mainly reduces global popularity, say “Include more lesser-known artists,” not an unsupported promise of genre diversity.
- Show the saved value and a discreet updating state. Commit once after drag/key interaction, with bounded debounce for repeated key changes. Do not regenerate every playlist on each pointer pixel.
- Indicate exactly which shelves the control affects. Update those shelves in place; do not imply the whole page has changed when only two artist lists respond.
- Failed persistence restores the prior value or leaves an explicit unsaved state with Retry. In-flight older saves cannot overwrite the latest value.
- Stop decorative animation when off-screen, hidden, reduced motion, or not changing. Avoid a 60 Hz React rerender for an idle control.

**Acceptance:** Arrow keys, Home/End, touch drag, and screen reader value announcements work. Reduced motion produces no animated waveform. A failed save is visible. Repeated drags keep only the last result. Existing cards remain usable while recommendations update.

### M06 — Replace pseudo-intelligence with auditable recommendation reasons [P1/P2]

**Observed:** `discoveryInsight` chooses “Fill the newest gaps first” whenever a count is greater than zero. The page calls this “Next best move.” Counts combine different datasets without deduplication. The hero frequently says only “Similar to an artist in your library.” “High-confidence” is used without a surfaced confidence contract.

**Change:**

1. Remove claims of confidence/optimality until a real ranking reason supports them. A nonzero album count is not evidence that filling gaps is the user's best action.
2. Preserve item-level provenance: source, seed artist(s), explicit taste signal, freshness, novelty, availability, and exclusion reason when suppressed.
3. Reason examples: “Because you played [artist] recently,” “A new album from an artist you follow,” “Related to [A] and [B],” “A lesser-known release in [genre].” Use only signals actually available.
4. “Why this?” opens a compact explanation with Like more / Not for me / Hide this artist. Distinguish hiding an artist from disliking one track.
5. Deduplicate by canonical release/recording/artist identity with source mappings. Names alone are insufficient; do not merge remasters, editions, or unrelated artists merely because strings resemble each other.
6. Cap artist concentration in a general discovery mix. Start with no more than two tracks per artist in a 20-track discovery set unless the user explicitly chooses an artist-focused mix. Make this configurable in the generator rather than hard-coded in rendering.
7. Audit the live BYLT output: two Ariana Grande seed shelves appeared, and multiple seed rows were dominated by tracks attributed to PNAU. This is a **quality investigation**, not proof that those identities are wrong. Trace source identity, seed duplication, and aggregation before changing matching.
8. Store impression and explicit feedback signals locally/within the existing app architecture where appropriate. Do not silently infer dislike from failure to click or from a failed playback.

**Acceptance:** Every personalized reason maps to a real input. Two sources returning the same release do not inflate visible counts or repeat it adjacent to itself. A user can dismiss one recommendation and Undo. A seed-specific shelf has a distinct useful candidate set. A cold-start profile never receives invented “because you listened” copy.

### M07 — Reorder For you around finished outcomes [P1]

**Proposed default order:**

1. Optional compact resume/recent listening item if real resumable state exists.
2. One feature with direct listening/detail action.
3. **Made for you**: four to eight distinct mixes, with clear names and differences.
4. **New from artists you follow**: release-focused, date-aware, no unrelated seasonal bucket mixed into it.
5. **Because you listened to [artist]**: at most two high-quality seed shelves on the landing view.
6. **Try something different**: a deliberately varied set, with a small taste-tuning affordance nearby.
7. **Artist radio**: a compact station rail.
8. **Complete an album / Collection discoveries**: a small preview with See all, not the entire management interface.
9. An explicit “Explore more” route/link, not an unbounded succession of controls.

Keep Explore, Collection, and New releases useful as independent destinations. Do not remove map/label/era/radio features merely to shorten For you; move them into coherent homes.

**Acceptance:** A first-time viewer can name the purpose of each of the first three shelves. General mixes have clear differences in composition rather than identical 50-track labels. No two consecutive shelves are functionally identical. No global source setup block dominates a populated listener's landing page.

### M08 — Separate New releases from collection gaps [P1]

**Observed:** “New & Missing” combines recent releases, genres, seasonal albums, undiscovered albums, label exploration, and Your Albums. The displayed album metric is a sum of only some arrays, so it is neither all visible albums nor necessarily missing albums. The “Review gaps” shortcut therefore overpromises.

**Change:**

- **New releases:** chronological/relevance ordering, real release date, Album/EP/Single distinction, follow source reason, optional format/explicit filters when supported.
- **Collection:** missing releases or tracks, owned coverage, edition identity, acquisition state, and reviewed bulk actions.
- Seasonal content belongs under editorial Explore or a seasonal mix feature; label exploration belongs under Explore/Labels.
- Do not call every fresh release a gap. Determine ownership at the appropriate edition/recording scope before showing “Missing.”
- Count unique entities in the currently represented result set. Say “27 releases” only if 27 distinct releases are actually accessible; otherwise omit the metric. For paginated results, distinguish total known from loaded count.
- Offer “Hide already in library” and “Show missing tracks only” in Collection where supported; keep them distinct from recommendation dislike.

**Acceptance:** Newly released owned album can appear under New with Owned state; it cannot be counted as missing. Standard vs deluxe editions have an explicit relationship and no accidental duplicate acquisition. Seasonal albums do not inflate a missing count. A zero-gap collection shows a useful completion state rather than a fake next-best-action prompt.

### M09 — Make Follow, Save, Monitor, Download, and Sync distinct [P1]

**Observed:** Add to Watchlist dominates artist recommendations, Watch All is offered, and Sync is the primary action in at least one mix. Users must understand internal concepts before knowing what happens next.

**Change:**

- Inventory existing semantics before renaming: document exactly whether artist Watchlist scans metadata, monitors new releases, queues downloads, or does some combination under settings.
- If Watchlist means monitoring, label the visible action **Monitor releases** or **Follow artist** with an explicit short explanation on first use and in settings. Do not change behavior invisibly by changing copy.
- Save an album/mix for later must have a distinct persistence path if one does not exist. Mark this as a backend addition, not a local temporary heart that disappears on reload.
- Download/Get opens a summary of what will be acquired, quality/profile where relevant, missing versus already owned, destination, and queue behavior. Keep advanced rules collapsed but available.
- Sync names the destination: “Sync playlist to Plex” or the actual selected server. Explain whether missing tracks will be acquired, skipped, or left unresolved.
- Batch following/downloading requires selection, visible count, and one clear submission. Prefer a compact in-flow review over multiple confirmation dialogs. Existing user configuration should prefill the defaults.
- After a job is accepted, card state becomes queued/in progress with a durable progress link; refresh and navigation preserve it.

**Acceptance:** Ask a user to explain each action before clicking: Save never sounds like Download; Sync names its destination. A batch selection containing owned and missing rows reports the distinction. A successful server acceptance is not called a completed import. Retry is idempotent.

### M10 — Give music navigation stable URLs and return state [P1]

**Change:**

- Use the existing router for intent views and significant detail surfaces. Suggested semantic shape: `/discover?view=for-you`, `view=new`, `view=explore`, `view=collection`; exact schema should fit existing route conventions.
- Shelf See all includes a stable shelf key and filters in URL state. Use stable keys, not translated titles, generated indexes, or arbitrary raw API query strings.
- Store origin shelf, item key, vertical anchor, horizontal rail position, filter/sort state, and loaded pagination state in session/navigation state. Keep sensitive or very large result payloads out of the URL.
- Back from artist/album/mix detail returns to the exact item neighborhood, including horizontal rail position. Do not force the user to start at the hero again.
- Opening Artist Map/Web must not destroy the user's discovery context. Closing returns focus/scroll to its entry; selecting an artist from a graph allows returning to the graph's selected node and viewport.
- “Build playlist” must focus the actual builder input or open its route. Current `scrollToDiscoveryTarget` only scrolls, with no missing-target feedback or focus transfer.

**Acceptance:** Open a card in rail 4 after scrolling horizontally, inspect detail, Back: same rail/card. Reload a See all URL: same filter. Open graph, close graph: same originating view. Browser forward works. Unknown shelf key falls back with an explanation rather than a blank page.

### M11 — Create a coherent Explore destination [P1/P2]

**Change:**

- Group entry points by intent: Genres, Eras, Labels, Artist connections, Radio, Build a playlist.
- Replace “Artist Map” versus “Artist Web” implementation vocabulary with outcomes. Example: **Explore related artists** and **See my library's connections**. Explain the difference in one sentence and avoid six equally prominent graph variants before a user understands the first.
- Graphs are optional advanced experiences. Provide an equivalent list view for keyboard/screen-reader users and low-power devices.
- A graph empty state names the prerequisite: no library, no mapped artists, or no recommendation data. Do not display an empty cosmic canvas indefinitely.
- Playlist builder: search seed artists, identify selected sources/artists, tune size/novelty if supported, generate, then Play/Save/Get missing. Keep selected seeds after failure and retry.
- Existing source/service setup belongs in a clearly named connection panel. Provider logos identify provenance; they are not the top-level navigation model for finding music.

**Acceptance:** Users can choose an exploration method without knowing the application's graph architecture. Map and list reach the same artist details. A failed graph does not prevent genre/radio use. Builder retains seeds after a network failure and cannot submit duplicates while generating.

### M12 — Make loading and freshness trustworthy [P1]

**Observed:** Music shelf queries use infinite stale and garbage-collection times, no retry, and a first-tier gate. This preserves quick revisits but makes freshness depend on explicit invalidation/reload. The source comments emphasize migration fidelity over a current freshness contract.

**Change:**

- Keep cached content immediately visible, with a deliberate freshness policy per data family. Static genre taxonomies can live longer than ownership, progress, new releases, and personal taste.
- Invalidate relevant entity/section state after save, follow, acquisition, import, source change, profile change, and explicit refresh. Verify the existing shell profile reset behavior before alleging a leak or adding duplicate cache clearing.
- Key personalized queries by profile/source/library context or prove that a full scoped cache reset happens on context change. Add a regression test for A→B→A.
- First useful listening rail must not wait for unrelated recommendation requests. The present tiering should be updated to match the new actual above-fold layout.
- Expose per-section loading, empty, disconnected, stale, and failed states. One broken provider does not remove all discovery or produce a wall of error toasts.
- Retry is explicit on failed shelves, with bounded automatic retry only for appropriate transient failures. Preserve existing cards while refreshing.
- “Updated [time]” belongs in a detail/refresh context unless staleness materially affects the current decision. Do not add timestamps to every card.

**Acceptance:** Returning to Discover is quick; importing an album updates ownership without a full browser reload; source failure shows useful cached content; a profile switch shows the correct person; first listening choices render while a nonessential provider is slow. Measure request count and time-to-useful-content before and after.

### M13 — Fix release identity and metadata quality at the source [P1/P2]

**Change:**

- Preserve canonical artist IDs, release IDs, recording IDs where present, source IDs, title, version, year, and explicit/edition metadata through normalization and playback resolution.
- Treat similar names as candidates for resolution, not proof of identity. Keep classical movement names, featured artists, punctuation, and non-Latin text.
- When only artist/title is available, mark resolution confidence and expose the selected match before acquisition if ambiguity is material. Do not silently map to a cover/remix/live version because its title is similar.
- A release detail should say Album, EP, Single, Deluxe, Live, Remaster, or Compilation where supported. Do not create badges from guesses.
- Trace BYLT duplicate seeds and repetitive artists through backend aggregation. Deduplicate seeds by identity and diversify candidate sets before rendering.
- Add diagnostics for developer inspection without exposing internal metadata terminology throughout the user flow.

**Acceptance:** Fixture suite includes same-title songs by different artists; standard/deluxe versions; remixes; non-Latin titles; featured artists; two artists sharing a name; classical titles. Playback/download points to the intended entity or requests a meaningful choice. No silent “best guess” acquisition for an ambiguous match.

### M14 — Make Collection tools precise and recoverable [P1/P2]

**Change:**

- Keep album search, source selection, ownership filter, and sort in Collection, with active filter summary and clear reset.
- For an incomplete album, show “8 of 12 tracks in library” and **Get 4 missing tracks**, with edition awareness. If exact coverage is unknown, say so.
- Expose quality and destination in an expanded acquisition summary, not on every discovery tile. Respect existing quality policy rather than inventing a discovery-specific override.
- Bulk operations show selected count and per-item outcome: queued, already owned, no match, failed, waiting. Allow retry only failed items and preserve successful ones.
- Imported/ready state updates every occurrence of that entity. Downloaded bytes alone are not proof of successful library import or playability.
- Do not create a second disconnected job tracker. Reuse the existing DownloadBar/job/status machinery after clarifying its contracts.

**Acceptance:** Mixed-success batch can be resumed after navigation/reload; successful tracks are not downloaded again; status reflects import completion; one unavailable release does not fail the entire batch; progress is understandable without reading logs.

### M15 — Music cold start, disconnected services, and empty states [P1]

Implement these as separate states with real copy and actions:

| State | Message direction | Action |
|---|---|---|
| New profile, no listening history | “Pick a few artists to shape your recommendations.” | Choose artists; optional Skip to genres |
| Library connected, not scanned | “Your library is still being indexed.” | View scan progress; browse public catalog if supported |
| Recommendations unavailable, library playable | “Personal recommendations are unavailable right now.” | Play library radio; Retry recommendations |
| One source disconnected | Name the source and what is affected | Reconnect from connection settings |
| No new releases | “You're caught up on releases from followed artists.” | Explore related artists |
| No missing albums | “This view has no missing releases.” | Clear filters or browse new music |
| Search/filter has no results | State the query/filter context | Clear a specific filter; preserve other choices |
| Mix resolved to no playable result | Explain whether metadata, capability, or availability failed | View tracks, Get missing if supported, Retry |

Do not use the same “Nothing to show” message for all cases. Do not show setup prompts to established users on every visit.

### M16 — Music success criteria and usability script [P1]

Run these tasks with realistic data, not only ideal fixtures:

1. “Play something based on an artist you listened to recently.” Success: direct station/mix activation, no confusion with downloading.
2. “Hear one track from this mix before committing to it.” Success: row action works and has accurate labeling.
3. “Save this album for later without downloading it.” Success: durable saved state; no acquisition side effect.
4. “Find a new release from an artist you follow.” Success: date and artist relationship clear.
5. “Get only what is missing from this album.” Success: exact scope and recoverable progress.
6. “Find a more adventurous recommendation, then undo a dismissal.” Success: tuning has defined scope and feedback persists.
7. “Open an artist, go back, and continue where you were.” Success: position and focus restored.
8. Complete 1, 2, and 7 with keyboard only and on a 390 px touch viewport.

Provisional target: at least 90% task completion in moderated testing without instructor explanation; zero unintended acquisitions; median time to first deliberate listening action under 15 seconds for returning users with playable content. These are proposed targets, not measured current results.

## 7. Video Discover: detailed implementation work

### V01 — Preserve the cinematic quality, repair the opening decision [P0/P1]

**Observed:** The Silo hero is visually attractive: real wordmark, useful backdrop, decent text scrim. It occupies most of the first desktop viewport. Under it, Discover says “Movies & shows you don't have yet,” while the feature says In your library twice. The available actions are More info and Trailer. Mobile carousel dots become tall pills over Trailer.

**Change:**

1. Put page context and intent controls above the hero: **For you**, **Movies**, **Shows**, **Available now**, **Explore**. Avoid duplicating the app's top-level Music/Video switch.
2. Reduce desktop hero starting height to approximately 360–440 px, constrained by content. Mobile should remain a compact feature rather than a billboard plus controls stacked over each other.
3. Retain wordmarks with maximum width/height and readable fallback title. They cannot remove the accessible heading. Use aspect-ratio preserving images; never stretch a narrow logo to fill the available height.
4. Show one state phrase, not “In your library” in both eyebrow and metadata. Use the eyebrow for a useful reason: Continue watching, New season, Because you liked [title], or Trending on TMDB.
5. Primary action is capability driven: Resume [episode] if known; Play if playable; Open in [server/provider] if that is the supported action; View details if playability is unknown or unsupported. Do not assume library presence proves playable episodes exist.
6. Trailer is secondary and only enabled when a trailer is known or when its lookup is explicitly pending. No autoplaying trailer on hover.
7. Put all carousel controls in a reserved bottom row separated from CTAs. Reset inherited button min-height/width/padding correctly; implement a small inner dot inside a large accessible target. Respect mobile line wrapping.
8. Replace contradictory page subtitle with “Find your next movie or show” unless the view actually enforces missing-only results.
9. Remove automatic rotation by default, or follow the complete pause behavior in section 5.5. Current video reduced-motion support should remain; add keyboard focus pause and stable manual selection.

**Files:** `video-discover.js` `renderHero`/`paintHeroBody`/timer functions; video discover markup; `.vdsc-hero-*`, `.vdsc-dot*` styles.

**Acceptance:** At 390 px, Trailer and indicators never intersect. At 1440×900, first personal rail begins within the first viewport. Owned-but-unplayable title does not offer a false Play. An actual resumable episode is named accurately. Logo failure falls back without losing the title. Hero stays stable while a button has keyboard focus.

### V02 — Make browsing race-safe and restoreable [P0/P1]

**Source-confirmed risk:** `openCategory` replaces `state.cat`. `loadGrid` closes over the old object but later inserts into the shared `[data-vdsc-grid]`. There is no active-generation check before inserting. Fast filter changes with out-of-order responses can therefore mix old results into a new query. This was identified in source, not deliberately induced against the live service.

**Observed:** Entering a category leaves the URL at `/video-discover`. Browse reload returned to shelves. `closeCategory` changes visibility without a recorded origin scroll/rail anchor.

**Change:**

- Give each result query a monotonically increasing generation/request key, plus AbortController where supported. Rendering requires both query identity and active generation to match.
- Key query identity from canonical normalized filter state, including kind, sort, genre, provider, original language, region, ownership filter, and preference revision where defaults are inherited.
- Start a new query by preserving useful current results with a clear updating state or replacing with a stable skeleton; never append new-query results into old-query cards.
- Serialize pagination for one query; prevent duplicate page requests. Validate server `next_page`/`has_more` and deduplicate entity keys across returned pages.
- Handle aborted requests silently; handle real failures visibly with Retry. Do not show “No results” for HTTP 500, timeout, or malformed payload.
- Store active Browse/category state in URL parameters. Store origin shelf and rail/scroll anchor in history/session state. Browser Back from category returns to the exact shelf, not the previous unrelated app route.
- Restore query after refresh. Deep links must reconstruct state without relying on prior shelf initialization.
- Back/forward must not restart acquisition actions or reapply preference writes.

**Acceptance:** Controlled test: A delayed 2 seconds, B delayed 100 ms, choose A then B; only B renders. Repeat with page 2 of A pending and with closing the category. Back and reload restore active filters and position. Duplicate records across pages appear once. A failed next-page request preserves existing cards and retries that page.

### V03 — Make Not interested and preferences transactional [P0/P1]

**Observed/source:** `wireNotInterested` fires `postIgnore` without awaiting it, fades/removes the card, and offers no rollback. Only the clicked occurrence is removed. Language/provider preference saves rebuild rails after any resolved fetch, without checking HTTP/application success. Session-cached list URLs do not include those inherited preference values.

**Change:**

1. Treat Not interested as a personal recommendation action. Store it by `(kind, tmdb_id)` and profile where supported. Investigate current shared/global settings before changing scope; migrate deliberately.
2. Optimistic hide is acceptable only with a retained prior state, save acknowledgment, rollback, error feedback, and Undo. Remove every visible occurrence of the entity from recommendations, not just one DOM element.
3. Do not force permanent exclusion for “Not tonight.” Offer a lightweight “Hide this recommendation” with Undo; keep a separate “Don't recommend this title” if permanent behavior exists. Avoid overloading X as permanent taste feedback without explanation.
4. Rename Ignore List to **Hidden titles** in personal discovery. Keep technical ignore/block terminology in admin contexts only if its behavior differs.
5. On failed save, restore the card at a sensible position, retain focus, and say “Couldn't hide [title]. Try again.” No silent disappearance.
6. Preference changes use pending/saved/error state. Coalesce rapid edits into one latest save; older save responses cannot restore older settings.
7. Invalidate or re-key cached lists when inherited language/provider preference changes. A rail with the same URL must not keep a result set from the old language selection.
8. Undo invalidates all affected views and survives a rerender. Closing a notification must not cancel the underlying save.

**Acceptance:** Simulated 500/timeout leaves accurate state and actionable recovery. Hide title appearing in two rails removes both after acknowledgment. Undo restores recommendation eligibility. Changing EN to KO with an already populated session cache actually requests/renders the new context. No repeated-write race reverts the newest preference.

### V04 — Replace the long shelf directory with a purposeful home [P1]

**Observed/source:** `buildSections` defines ten groups and many static shelves: trends, new releases, genres, moods, studios, eras, language slices, and more. The genre directory and jump bar precede meaningful rails. A provider rail may be the first “For you” content despite being popularity-based and full of owned titles. More content does not necessarily create better discovery.

**Proposed For you order:**

1. Compact Continue watching when supported by trustworthy playback state; do not create duplicate large resume widgets if the shell already supplies one.
2. One personal/editorial feature, governed by V01.
3. **Picked for you**: 8–16 diverse candidates with readable reasons and availability.
4. **Ready to watch**: relevant, actually playable local/server items; show new-to-user rather than assuming owned means seen.
5. **New from your shows / New arrivals**: distinguish fresh episodes, new seasons, catalog arrivals, and newly released titles.
6. **Because you liked [specific title]**: one or two selected seeds; preferably explicit positive/watching history rather than arbitrary ownership.
7. **Complete a collection**: focused franchise gap preview with owned/missing scope.
8. **Try something different** or one curated mood shelf.
9. Explicit Explore all link.

Move studio, full genre, decade, and language directories into Explore. Keep them fast to reach from the intent navigation. Show a finite landing page with a clear endpoint and useful next action rather than a feeling of endless duplicated catalog.

**Acceptance:** First three rails answer different user needs. The same title does not dominate hero + first two rails. Users can reach any genre in at most two actions. An empty personal recommendation set degrades to clearly labeled trending/genre content without claiming personalization. Both movies and shows receive meaningful exposure.

### V05 — Build a real “What should I watch tonight?” flow [P2]

**Change:**

- Add a compact optional decision control: available now, movie/show, time available, mood, alone/together where an actual profile feature exists. Avoid a long survey.
- Start with deterministic filters and ranking. Do not require an LLM or invent natural-language promises the backend cannot satisfy.
- Time applies to movie runtime or the next episode, not total series length. Missing runtime remains unknown; it cannot pass a strict “under 90 minutes” filter as zero.
- Return a shortlist of three strong options with reason, runtime, content rating if known, language/accessibility availability if known, and the actual watch action.
- Actions: Play/Open, Details, Save, Another three. Another three excludes already shown choices for the session and retains constraints.
- “Together” is only shown if the product can actually represent group/profile preferences. Otherwise defer it; do not approximate a household by assuming a shared library means shared taste.
- Keep this as an optional accelerator. Users can still browse freely.

**Acceptance:** User selects 90 minutes + available now and receives only eligible known-runtime choices, or a truthful empty state with one constraint to relax. No rental masquerades as an included subscription. Another three produces a genuinely different shortlist without dropping filters.

### V06 — Make Browse powerful without looking like a control wall [P1]

**Observed:** Browse shows multiple rows of tiny colored chips: kind/sort, genres, services, decades, languages. Selected/default values are visible, but most nonselected text is dim. There is no strong summary of the result intent. Shelves' See all hides the filter bar rather than offering a clear way to refine the category.

**Change:**

1. Primary compact row: Movies/Shows, Sort, Filters, result count when known. Active filters appear as removable chips below; a “Clear all” action resets only query filters, not saved personal preferences.
2. Filters panel/drawer uses named groups with readable selected state. Desktop can use a compact sidebar or popover; mobile uses a full-height sheet with Apply/Show results and Clear. Do not force every filter into the page top.
3. Required filters: type, genre, original language, release year/range, availability/library state, service + region. Add runtime and certification using existing backend support. Keep unsupported show-only/movie-only fields disabled or absent with an explanation.
4. Original language is not audio track language or subtitles. Label it explicitly; add audio/subtitle constraints only when verified data exists.
5. Support multiple genres/services where the backend can support them, with documented semantics: “Any selected genre” versus “All selected genres.” Do not let comma/pipe backend syntax dictate unexplained UX.
6. Type switch resets or maps incompatible genre IDs. Do not send a movie-only Action ID as a TV genre without a defined mapping. Preserve compatible filters and explain any removed constraints.
7. Stable sort choices: Recommended where supported, Popular, Top rated with confidence floor, Newest release, Runtime. Distinguish vote count from rating.
8. A shelf category supports “Refine results” starting from that shelf's actual constraints. If a curated query cannot be represented, explain that and offer Browse similar; don't show controls that silently discard its meaning.
9. Results update in place. Focus stays on the selected control and an accessible result-count/status announcement follows; avoid scrolling to the top on every chip click.

**Acceptance:** Users can state active constraints without reopening every filter group. On mobile, first result row remains visible after applying filters. Unsupported filters do not produce mysterious empty lists. Rapid changes pass V02 race tests. Clear filters does not erase saved provider preferences. URL sharing/reload reproduces the same query semantics.

### V07 — Replace ambiguous card labels with useful availability [P1]

**Observed/source:** Card ribbons say In Library or Preview; NEW is determined by `year >= NOW_YEAR` for unowned titles. “Preview” does not tell whether a title is unavailable, external, or simply not owned. A star rating is shown without source. Poster cards embed Get and Not interested buttons inside an anchor.

**Change:**

- Use semantic card structure: article/container, poster/title link, sibling action buttons. No nested button inside anchor.
- Title, year, Movie/Series, and one concise availability line form the default text. Add runtime where useful and reliably known.
- Replace Preview with an actual availability state: Not in library, Available on [service], Upcoming, Checking availability, or no badge when none is useful.
- Replace year-based NEW with a real date/category rule: “Released this month,” “New to your library,” “New episode,” or “Coming [date].” Unknown date means no recency badge. A future year cannot mean already released.
- Ratings show provenance, e.g. TMDB 8.2, with vote count accessible in details/tooltip. Do not present it as a personal match percentage.
- Badges must contrast on white, yellow, and dark posters. Use a stable filled background/scrim instead of translucent white text over white art.
- Hover controls also appear on focus and are reachable on touch through an always-visible overflow/menu. Do not require hover to save or dismiss.
- Avoid badge stacks covering the title printed in the poster. Prefer one state badge plus optional progress indicator; put remaining details below the artwork or in detail.

**Acceptance:** Future release fixture never shows NEW as availability. Owned partial show reads Partial/episodes available, not a guarantee of full-series playback. Every control is operable independently by keyboard and touch. Rating source is identifiable. A white poster retains readable status.

### V08 — Turn save/acquire/monitor into a clear sequence [P1]

**Change:**

- Separate personal **Save for later** from library **Get movie/Get show** and automation **Monitor**. Document how the existing Wishlist and Watchlist map to these concepts before renaming UI.
- Get movie opens the existing acquisition flow with title identity, quality profile, destination, and current state prefilled. Default to normal configured behavior; advanced source/release choices remain accessible without crowding discover.
- Get show asks for scope: next episode, selected season(s), missing available episodes, future episodes. Choose defaults consistent with existing settings and never imply that selecting a show only obtains one episode if it will monitor all seasons.
- Show accurate existing state before submission: already queued, downloading, imported, awaiting release, partially owned. Prevent duplicate requests.
- When request approval is required by existing permissions, action reads Request, not Get. Show submitted/pending/approved/rejected state and reason where available.
- Acquisition failure offers a route to the existing diagnostics/progress UI with the same title/job ID. Do not reinvent the full downloader in Discover.
- A compact state summary on a card should be sufficient; technical indexer/release details belong in the expanded flow.

**Acceptance:** Saving causes no download. Getting a show exposes scope. Repeated click submits one operation. An already pending request is visible. Failed import does not become In Library/Ready merely because download finished. Returning later shows current state without re-opening acquisition.

### V09 — Separate personal taste from shared ownership [P1/P2]

**Observed/source:** Taste uses most-owned genres; More like seeds come from random owned titles; For you blends recommendations from random owned movies/shows. These are useful starting signals, but ownership is not the same as having enjoyed a title—especially in a family library. Language/provider preferences are stored through database settings in the reviewed routes; verify intended scope before redesign.

**Change:**

- Model personal explicit likes/dislikes, watched/progress state, saved choices, and shared library availability separately.
- Rank explicit positive preference above incidental ownership. Ownership may be a fallback signal, labeled accordingly (“Related to your library”).
- Carry seed provenance through `blend_recommendations` instead of returning only a candidate item after counting consensus. Explain the strongest one or two real reasons.
- Use typed `(kind, tmdb_id)` identity everywhere. Current aggregation deduplicates by kind+ID, but `exclude_ids` is an untyped set of integer IDs; inspect and change the seed-exclusion contract so a movie seed cannot exclude an unrelated TV entity sharing its numeric TMDB ID.
- Deduplicate a candidate within each seed recommendation list before counting cross-seed consensus. Multiple copies from one source list must not masquerade as multiple independent recommendations.
- Stabilize seed selection over a reasonable session/time window. Do not randomly reinvent the home page every visit; offer explicit refresh/new picks.
- Let users remove a seed from their taste without removing the media from the library.
- If profile-scoped taste is not yet supported, say “Based on this library” and treat profile personalization as a backend feature, not a copy change.

**Acceptance:** A family-owned children's title does not automatically become an adult profile's strongest positive taste signal when better personal signals exist. Typed ID collision fixture passes. Same candidate repeated within one source gets one vote for that seed. Reasons survive to the UI. A new profile gets honest fallback language.

### V10 — Add diversity and restraint to recommendation assembly [P2]

**Change:**

- Dedupe across the hero and first several shelves using typed entity keys, while preserving meaningful intentional reappearance such as Continue watching.
- Apply a shelf-level exposure budget: initial 8–16 items, more in See all. Current shelves commonly fetch two pages; the provider rail can become a very large wall. Optimize for useful visible choices rather than raw counts.
- Blend relevance with novelty, type balance, language preference, availability, and recent exposure. Do not replace all ranking with popularity.
- Limit franchise/person dominance within a generic shelf; permit concentration in a named franchise or filmography shelf.
- Distinguish hidden gems from top rated. A “Hidden Gems” query sorted only by vote average is not sufficient evidence of obscurity. Add a popularity/exposure ceiling or rename it.
- Mood claims must be justified. Comedy+Family is a rough candidate generator for feel-good, not proof of tone. Drama is not automatically a tearjerker. Use curated metadata/keywords with quality checks, or use literal genre labels.
- Keep international discovery intentional and inclusive. A saved original-language preference should be respected with visible exceptions, not described as foreign titles “leaking” into the feed.

**Acceptance:** First 30 visible discovery candidates have low unintended repetition; diversity constraints are unit-tested without breaking relevance. Mood shelves are sampled and reviewed. Hidden gems excludes obvious blockbuster dominance under its defined rule. Explainability remains correct after diversity reranking.

### V11 — Make charts and ranked shelves honest [P1]

**Source:** Top 10 uses TMDB daily trending, slices returned results, and renders rank as `i + 1`. With hide-owned filtering, visible items are renumbered. Hero “#N Trending now” is the index in a backdrop-filtered hero list, not necessarily the original trending rank.

**Change:**

- Label the source: “Trending on TMDB today,” not an implication of SoulSync viewing, worldwide total viewing, or a selected service's own chart.
- Preserve source rank in the API payload before filtering. If hiding owned items, show original ranks with intentional gaps or relabel the shelf as a filtered selection without rank numbers.
- Do not turn the first unowned entry from source rank 8 into “#1.”
- Hero uses actual source rank if available; otherwise just “Trending now.”
- Include period and relevant region/source context when meaningful. If no local chart exists, do not invent regional specificity.

**Acceptance:** Test source ranks 1–10 with owned ranks 1, 3, 4; remaining cards retain 2, 5, etc., or are explicitly unranked. Hero backdrop filtering cannot produce false ranks. Chart source is discoverable without opening each title.

### V12 — Make streaming availability useful, specific, and current [P1/P2]

**Existing strength:** Backend provider filtering already sends region and flatrate. Preserve that distinction.

**Change:**

- Display active region in the service filter/preferences context; allow correction through existing settings or a deliberate scoped control.
- Show which selected service carries each candidate when known. “On your streaming services” without naming the service helps less than it should.
- Distinguish included subscription from rent/buy/add-on when extending beyond current flatrate filtering. Do not claim universal availability based only on a provider logo.
- Name launch behavior: Open in Netflix, Open in Plex, Watch in browser, or View provider options. If deep linking cannot reliably target the title, label a provider page link honestly.
- Treat metadata lookup and account entitlement separately. Selected provider preference is not proof the user's subscription includes a title or that they are signed in.
- Refresh availability on a sensible schedule; detail can revalidate. If lookup fails, show last-known context and avoid blocking unrelated local playback.
- Verify `watch_region` versus other region setting names used by enrichment/backfill before unifying. Do not accidentally introduce competing region settings.

**Acceptance:** Same title in two regions has correct provider options. Service absent in region yields an explained empty result. Local ownership remains distinct from streaming subscription. A provider lookup failure does not silently mean unavailable everywhere.

### V13 — Add decision-grade details without forcing a full page visit [P2]

**Change:**

- Provide an intentional quick-detail drawer or compact preview, opened by a clear info action; avoid automatic hover explosions that cover neighboring cards.
- Minimum facts: title/year/type; synopsis; runtime or next-episode duration; content certification and country when known; genre; rating source; personal reason; availability; primary action; Save.
- TV adds seasons/episodes available, next episode if relevant, and ongoing/ended status. Avoid spoilers in episode summaries/thumbnails unless the user chooses them.
- Trailer opens a dedicated accessible player dialog. Pause hero motion while it is open; stop playback when it closes; restore focus to the opener.
- Trailer loading/error/unavailable has an inline state and Close; not a permanent spinner. Respect the user's chosen playback action and existing player policy.
- Full details remains a link for cast, technical metadata, versions, acquisition diagnostics, and advanced management.

**Acceptance:** A user can decide whether a movie fits tonight without navigating into three separate pages. Escape closes the topmost dialog only; trailer stops; focus returns. Unknown runtime/certification remains unknown. Opening quick detail does not reset the Browse grid.

### V14 — Make collection completion a differentiator [P2]

**Existing strength:** Gap routes already use collection and filmography helpers and filter ignored items in relevant paths.

**Change:**

- Show a franchise as a coherent collection feature: collection name/art, “You have 3 of 5,” missing titles, release order, and explicit upcoming/unreleased status.
- Separate **Missing from collection** from **More from this director**. The latter is an interest suggestion, not an objectively incomplete collection.
- Get missing opens a reviewed selection with quality/default acquisition scope. Do not blindly queue every unreleased installment or unwanted remake/spinoff.
- Saved personal exclusions should not be silently reintroduced by a gap shelf. Offer “Show hidden entries” only when the user intentionally inspects the collection.
- Cache gap data and revalidate after import. Do not make a heavy collection backfill block the initial Discover page; the current route does best-effort backfill, which should be measured and budgeted.

**Acceptance:** Owned, missing, upcoming, ignored, and queued entries are counted consistently. Collection progress updates after import. A user can exclude a particular installment without having it repeatedly proposed as a problem. Acquisition summary matches selected titles exactly.

### V15 — Unify errors, loading, cache freshness, and pagination [P1]

**Observed/source:** `cachedFetch` returns null for non-OK HTTP responses. `fillShelf` removes empty/failed shelves. Several personalized endpoints return successful empty payloads on exceptions. This isolates failures but obscures whether a user has no recommendations or a service failed. URL-only session cache may preserve stale ownership/preferences.

**Change:**

- Return typed section outcomes: ready, empty, not configured, stale, error, loading. Keep backend/internal error details out of normal copy; keep a correlation/diagnostic path for troubleshooting.
- Prioritize cached hero and first personal rail. Load the next viewport with a bounded concurrency budget. Preserve the existing IntersectionObserver strategy and explicit Load more fallback.
- Every retryable failed section has Retry in its reserved area; empty low-priority shelves can disappear, but a failed primary personal section needs an explanation or fallback.
- Cache remote catalog facts separately from mutable ownership/saved/job state. Restamp mutable entity state on revisit and after relevant events.
- Bound cache size/age and deduplicate in-flight requests for the same key. Do not retain unbounded results from every Browse combination for an entire long session.
- Preserve server `has_more` and `next_page`; do not return to client guesses based on item count. End-of-results is explicit. Failed pagination preserves current cards and scroll.
- Hero/meta failures should not hide a working Browse interface. Missing genre data is not always proof TMDB is unconfigured; distinguish provider outage from setup.

**Acceptance:** Simulated 500 does not show a false empty result. A source outage leaves local ready-to-watch choices usable. Filtered sparse pages continue correctly without duplicate requests. Long browsing does not grow DOM/cache indefinitely. Import and dismiss events update cached cards.

### V16 — Fix keyboard scope and card focus [P0/P1]

**Source:** A document-level ArrowLeft/ArrowRight handler changes the hero whenever the Discover page is visible, except for a few input tags. It is not scoped to focus inside the hero. Video cards also combine an anchor with nested buttons.

**Change:**

- Scope hero arrow handling to the hero's own focused controls/region. Arrow keys in rails, chip groups, sliders, or dialogs must retain their own meaning.
- Do not cancel global arrow-key behavior simply because the page is visible. Respect contenteditable and existing composite controls.
- Rails use a consistent focus model. Native links/buttons in normal Tab order are acceptable; if roving tabindex is introduced, implement expected arrow/Home/End behavior and document it.
- Scroll a focused item into view within its rail without unexpectedly scrolling the entire page or moving the hero.
- Give action labels entity context. Ensure focus ring is not clipped by overflow or covered by artwork/badges.
- Preserve a focused item's identity during asynchronous hydration and wishlist badge updates; avoid replacing its entire DOM subtree unnecessarily.

**Acceptance:** ArrowRight while a poster is focused does not change the hero. Every card and action can be reached without pointer input. Focus remains visible at the rail edges. A state hydration update does not dump focus onto body. Mobile touch controls do not depend on hover.

### V17 — Video empty-state and preference matrix [P1]

| Condition | Required behavior |
|---|---|
| No personal signals | Explain how to choose favorites; provide clearly labeled popular/trending choices |
| Entire matching catalog owned | Offer “Show titles in your library” or another filter; do not imply there is no catalog |
| No playable titles | Explain capability/library state; offer Save/Get where supported |
| Provider region yields no titles | Name the service and region; offer region/filter adjustment |
| New show, unaired episodes | Show Upcoming/air date when known; Monitor is distinct from immediate download |
| All candidates dismissed | Offer Hidden titles management or broader filters; do not silently undo dismissals |
| TMDB temporary failure | Retry and cached/local fallback; do not say the integration is unconfigured |
| TMDB genuinely not configured | Setup action with clear requirement; retain any independent library browsing |
| Pagination failure | Existing results remain; inline retry at the end |
| No trailer | Clear unavailable state; close/back action; no dead Play triangle |

### V18 — Video success criteria and usability script [P1]

1. “Find a movie I can watch now in under 100 minutes.” Availability and runtime must be accurate.
2. “Find a show similar to one I enjoyed, without repeating things I've hidden.” Reason and exclusions must be clear.
3. “Save this film for later without acquiring it.” No unintended acquisition.
4. “Get season 2 of this show, but not the whole series.” Scope is visible and exact.
5. “Find what's on one of my services in my region.” Source/region is clear, not inferred from an unlabeled logo.
6. “Open the third rail, browse further, inspect a title, go back, refresh.” Context survives.
7. “Hide this title, then change my mind.” Undo restores eligibility and state across rails.
8. Complete 1, 3, and 6 on mobile and by keyboard. Simulate a failed save and a failed result page.

Provisional target: at least 90% task completion without coaching; zero unwanted acquisitions; median time to a watch/save decision under 30 seconds for returning users with relevant data. These are proposed release goals, not current measured performance.

## 8. Competitive bar: what SoulSync must earn

These are reference capabilities and product goals, not a claim that SoulSync can copy competitors' catalog rights, infrastructure, or native-device reach.

| Reference | Verified reference capability / expectation | SoulSync requirement |
|---|---|---|
| Sonarr | Quality profiles, manual search, calendar, and failed-download handling are core advertised features. [Official Sonarr site](https://sonarr.tv/) | Discovery must preserve operational certainty: exact monitoring scope, clear failure/retry, accurate progress, no accidental duplicate acquisition. Attractive cards cannot replace this. |
| Radarr | Automated movie collection management is its central product purpose. [Official Radarr site](https://radarr.video/) | Movie Get/monitoring must be predictable and integrate existing quality/import rules. Collection discovery should lead into a trustworthy acquisition flow. |
| Netflix | Recommendations incorporate viewing/rating signals, and initial preference selection can seed a new profile. [Netflix recommendation explanation](https://help.netflix.com/en/node/100639) | Treat personal activity and explicit feedback as stronger evidence than shared ownership; offer real cold-start choices and explain recommendations. |
| Disney+ | Personal profiles separate watchlists and recommendations; navigation exposes Home, Search, Watchlist and content browsing. [Disney+ profiles](https://help.disneyplus.com/en-GB/article/disneyplus-en-pl-profiles), [navigation](https://help.disneyplus.com/en-GB/article/disneyplus-en-mc-navigate-app) | Separate personal intent from household media inventory and provide a coherent, device-appropriate route to saved items and fresh content. |
| Prime Video | Profiles provide separate recommendations, season progress, and watchlist state. [Prime Video profile help](https://www.primevideo.com/-/hi/help/ref%3Datv_hp_nd_nav?nodeId=GD8VJD2EDJ2GSNEC) | Profile-scoped progress and taste must be reliable. Provider availability must be distinguished from library presence and actual entitlement. |
| Apple TV | Home connects featuring, Continue Watching, watchlist, and immediate playback where available. [Apple TV Home guide](https://support.apple.com/guide/tv/start-watching-on-the-home-screen-atvbe160da08/tvos) | Preserve the user's place and surface an honest next action. A large owned-title hero with only More info does not meet this bar. |

**SoulSync's opportunity beyond those baselines:** unify personal discovery with your own collection, missing-item awareness, chosen services, and controlled acquisition. This is a proposed advantage to develop and validate, not an asserted unique feature no competitor possesses.

For music, the same bar applies without forcing video's structure onto it: direct listening, personal mixes, real previews, durable saves, edition-aware collection completion, and artist exploration that leads to music rather than endless graphs.

## 9. Cross-cutting implementation contracts

### S01 — Sidebar and global utility overlay behavior [P0/P1]

**Scope:** Shell behavior affects both pages. The desktop-to-mobile transition is a reproduced obstruction; fresh mobile initial layout works.

- Use one explicit responsive menu state machine: desktop expanded/collapsed, mobile closed/open. Derive presentation from both viewport and user intent; do not carry desktop-open into mobile-open accidentally.
- On crossing into mobile, default closed unless the user explicitly opened the mobile drawer. On returning to desktop, restore the user's desktop collapse preference.
- Mobile menu opener remains visible and labeled, with expanded state and controlled target. Drawer has a backdrop, focus containment, Escape/backdrop close, and restored focus.
- Utilities sit outside primary content hit areas. Hide onboarding nudges after dismissal and avoid unprompted repeat prompts for established profiles.
- While a modal is open, utility orbs cannot remain interactive above the modal or receive focus behind it.
- If a persistent player exists, reserve its height plus safe area. Bottom controls and final list rows must remain reachable above it.

**Tests:** Fresh mobile; resized desktop; orientation change; 200% zoom; menu open then dialog; player active then drawer; long sidebar scroll. Do not validate only absence of document horizontal overflow: the failing resized screenshots had visually blocked content, which a width check alone cannot catch.

### S02 — Unified entity state and mutation acknowledgment [P1]

Use a shared conceptual contract even if React and vanilla consume it differently. The following is a **proposed view model**, not a declaration that these fields already exist:

```ts
type DiscoverEntityState = {
  key: string;                 // typed, stable, source-aware identity
  profileId: string;
  libraryContextId: string;
  libraryPresence: 'unknown' | 'none' | 'partial' | 'complete';
  playback: {
    state: 'checking' | 'ready' | 'unavailable' | 'unsupported' | 'unknown';
    targetLabel?: string;     // Plex, browser, named provider, etc.
    reason?: string;
  };
  saved: boolean | null;       // null means unknown, not false
  monitoring?: { enabled: boolean; scopeLabel: string };
  acquisition?: {
    state: 'queued' | 'searching' | 'downloading' | 'importing' |
           'waiting' | 'ready' | 'needs-attention';
    operationId: string;
    reason?: string;
  };
  revision: string;            // monotonic/versioned within the server contract
};
```

Implementation rules:

1. Normalize server state once near the data boundary. Do not independently infer “owned” from different fields in hero, cards, and dialogs.
2. Mutable state hydration must not erase catalog facts. Catalog refresh must not erase an acknowledged save or job state.
3. Mutation response returns the accepted result, canonical entity state, and operation ID for asynchronous work. UI derives the next label from that response.
4. Idempotency key or equivalent server deduplication protects actions that create jobs. Button disabling alone is insufficient after reload/retry.
5. Old revision cannot overwrite newer state. Reject stale request generations in view code as well as handling server concurrency correctly.
6. Keep profile scope explicit. Global server/library ownership may be shared; personal saves and feedback must not accidentally be shared unless the product explicitly presents them as shared.
7. Do not introduce this entire schema if an existing shared store already meets the contract. Adapt the existing system and document the mapping.

### S03 — Error and optimistic-update contract [P1]

| Operation | Pending UI | Success | Failure | Retry/Undo |
|---|---|---|---|---|
| Save | Button busy or optimistic Saved | Canonical Saved everywhere | Restore prior state; inline/toast explanation | Retry same intent |
| Hide | Optional fade after optimistic update | Suppressed across recommendation surfaces | Restore occurrence/position and focus | Undo restores eligibility |
| Start playback | Resolving/Starting | Queue accepted, then Playing when acknowledged | Name unavailable capability/track/target | Retry or choose target |
| Get/Download | Submitting | Queued + operation link | No false queued badge | Idempotent retry |
| Monitor | Saving scope | Monitoring with scope | Prior state restored | Retry exact scope |
| Filter | Updating existing result set | Latest matching query | Preserve previous results + error | Retry query |
| Load more | Footer loading | Append unique items | Retain list and pagination cursor | Retry failed page |
| Refresh | Current content remains | New snapshot reconciled | Show last successful content | Retry section |

A network failure, HTTP failure, business-rule rejection, and empty result are different outcomes. All four must be represented without forcing the user into developer logs.

### S04 — CSS and component cleanup [P1]

- Locate the effective selector cascade before editing. Both stylesheets contain years of shared rules; adding another override at the bottom is not a finished design system.
- Introduce scoped discovery tokens/primitives and remove obsolete rules only after identifying consumers. Generic `.btn` or `button` mobile min-height can affect tiny carousel indicators; verify computed styles.
- Preserve shared video search/detail card behavior when changing `.vsr-*`. Either coordinate shared changes with regression snapshots or scope discovery-specific differences.
- Resolve duplicate layout authorities. Music's explicit zone composition and `DISCOVER_LAYOUT` must have a documented single source of ordering truth; do not leave contradictory comments and tests.
- Update migration/differential tests that intentionally enforce old behavior when the behavior is changed by this specification. Preserve tests for meaningful data and action contracts; remove obsolete expectations rather than forcing new UX to mimic known defects.
- Do not delete features or replace genuine data with attractive static fixtures to satisfy screenshots.

## 10. Performance and perceived-speed acceptance

The review did not run a controlled performance benchmark. The following are initial budgets to measure and refine on documented hardware/network/data sizes.

### 10.1 User-facing budgets

| Measure | Initial goal | Measurement notes |
|---|---|---|
| Warm navigation to usable discovery content | ≤1 second in the local reference setup | Record navigation to first real, actionable card, not only shell paint |
| Cold navigation to useful cached/library choice | ≤2.5 seconds in the reference setup | Separate slow external enrichment from the critical path |
| Input-to-visible-feedback | ≤200 ms for normal interactions | Busy state should appear promptly even if operation is slow |
| Local playback start after accepted action | Target ≤2 seconds where current player/server supports it | Measure actual playback acknowledgment, not toast time |
| Layout stability | CLS ≤0.1 where applicable | Include late shelves, fonts, cover loads, and hero assets |
| Browser responsiveness | INP ≤200 ms at the 75th percentile when field data exists | Do not infer field results from one headless run |

The common LCP/INP/CLS thresholds and field-measurement framing come from [Web Vitals guidance](https://web.dev/articles/vitals). The SoulSync-specific action/start budgets above are proposed product targets.

### 10.2 Engineering checks

- Measure request count and payload sizes for first viewport, first three screens, and a ten-minute browsing session.
- Keep heavy backend queries bounded. Preserve the music limiter and video lazy loading, then prioritize them for the redesigned first screen.
- Prevent N+1 remote metadata calls merely to paint small cover mosaics. Hydrate visible content first and share in-flight lookups.
- Do not render thousands of cards because the sentinel remains visible or a large provider result set was cached. Bound pages/DOM, use deliberate virtualization where justified, and retain accessibility/focus.
- Pause background timers, observers, and decorative animation when hidden. Disconnect observers and abort irrelevant work on route exit.
- Measure low-powered/mobile CPU with the adventurousness wave, hero rotation, and multiple rails present. A dark theme does not compensate for dropped frames.
- Cache images at appropriate sizes; preserve aspect ratio and avoid layout shifts. Test first-view loading and repeated view cache hits.
- Keep old content visible during revalidation. A fast blank screen followed by shuffled shelves feels worse than stable slightly stale content with a refresh cue.

## 11. Test fixtures and coverage Claude must add or update

Tests must validate the user contract, not only CSS class names or the current implementation's own calculations.

### 11.1 Deterministic data fixtures

**Music fixtures:**

- Populated profile with owned, missing, partial, and unresolved tracks in one mix.
- New profile with no history but a connected library; completely empty profile; disconnected recommendation source.
- Two sources identifying the same album; standard/deluxe/live/remix editions; same-name artists.
- Long classical movement titles, accented characters, Japanese/Korean text, explicit metadata, unknown duration.
- Missing cover, broken cover, bright cover, low-resolution cover, slow cover.
- Followed artist with no new releases; new owned release; album partially imported.
- Playlist sync accepted then failed; some tracks matched and others unresolved; job resumes after navigation.

**Video fixtures:**

- Owned/playable movie; owned/unplayable movie; partly owned show; next episode available; no next episode.
- Upcoming movie, future year, unknown release date, newly added old film, new season of old show.
- Same numeric TMDB ID in movie and TV namespaces.
- Candidate duplicated within a seed and across seeds; same title repeated in several shelves.
- Streaming provider present in one region and absent in another; subscription versus rental if rental support is added.
- Empty after hide-owned; all ignored; no configured metadata provider; temporary provider outage.
- Wholly white poster; broken wordmark; very long title; unknown runtime/certification.
- Sparse filtered pages with server next-page jumps; duplicated items across pages; out-of-order responses.

### 11.2 Required automated behavior checks

1. **Music preview regression:** click an exposed row action and assert the correct player/preview contract receives the exact track; no-op callback fails the test.
2. **Playback truth:** absent bridge, rejected queue, empty resolution, partial resolution, and successful start produce distinct UI outcomes.
3. **Modal contract:** initial focus, Tab/Shift+Tab containment, Escape, backdrop close, focus restoration, body scroll lock. Inspect user-visible behavior, not only `role` attributes.
4. **Native activation:** card links/buttons activate with Enter/Space as appropriate; no hidden dependency on mouse click.
5. **Slider contract:** keyboard/touch/pointer updates value; persistence failure; out-of-order save; reduced motion; off-screen animation suspension.
6. **Latest-query-wins:** deterministic delayed A/B responses; old page-2 response; close/reopen; rapid type/filter switching.
7. **Navigation restoration:** shelf → category → title → Back; reload category; forward; horizontal rail position; profile changes.
8. **Optimistic mutations:** hide/save 200, 500, network rejection, timeout, duplicate click, Undo, same item in multiple rails.
9. **Mutable entity state:** acquisition/import updates cards and hero without full reload; stale catalog cache cannot undo it.
10. **Identity/rank:** movie/TV ID collision, duplicate consensus vote, future-year NEW, filtered Top 10 source-rank preservation.
11. **Error versus empty:** HTTP failure cannot appear as “Nothing found”; disconnected source has correct setup action; partial failure preserves other shelves.
12. **No implicit acquisition:** Play/Save semantics are tested against actual bridges. If missing-track preload intentionally acquires content, the UI disclosure and scope must match; do not mask it as a simple bookmark.

### 11.3 Visual and responsive checks

Capture both pages in these exact scenarios:

- Desktop first fold at 1440×900 and large desktop at 1920×1080.
- Small desktop/tablet at 1024×768 and 768×1024.
- Mobile at 390×844 and 320×740, fresh load and desktop resize.
- Long-title hero; no artwork hero; white-poster card; loading/error/empty states.
- Mix dialog on desktop/mobile with a 100-character track title.
- Video filters open/closed with multiple active values; provider preferences; quick detail/trailer.
- Menu open/closed with player active and utility bubbles present.
- 200% text zoom, reduced motion, keyboard focus on every action family.

Visual assertions must include **geometry and hit testing**, not just pixel snapshots: action rectangles cannot overlap; focus ring must be visible; each target can be activated; modal content cannot be behind a floating utility; first useful rail starts within the target viewport.

### 11.4 Manual checks still required

- Audible music playback and video/player handoff on supported targets.
- Real download/import lifecycle in a safe test library, including failure and retry.
- At least one screen reader/browser combination; automated accessibility tools are supplemental.
- Touch device or realistic touch browser checks for rails, sliders, menus, dialogs, and back navigation.
- Subjective recommendation sampling: inspect at least 30 candidates per main personalized surface for duplication, bad identity, weak reasons, and obvious irrelevant concentration.
- Moderated user tasks from M16/V18. Record task success, hesitation, misinterpreted verbs, and unintended actions.

## 12. Ordered implementation batches for Claude

Do not mark a batch complete because its code compiles. Each batch includes runtime evidence and specific acceptance results.

| Batch | Scope | Dependencies | Required deliverable |
|---|---|---|---|
| **1 — DONE** (see DISCOVER_BIC_PROGRESS.md) | M01 collisions, M02 preview/play truth, M03 modal behavior, M04 native controls, M05 accessible input, V01 indicator fix, V02 race guard, V03 save rollback, V16 keyboard scope, S01 resize | Existing app baseline | Small corrective patches, focused tests, matching before/after screenshots |
| 2 — Establish action/state model | M09, V07/V08, S02/S03; capability and saved-state mapping | Batch 1 | Written mapping of existing APIs to visible verbs; typed state adapter; no duplicate player/downloader |
| 3 — Music recomposition | M01 opening redesign, M03 visual dialog, M04 card system, M07/M08/M10/M11, shared tokens | Batches 1–2 | For you/New/Explore/Collection flows; desktop/mobile evidence; preserved current features |
| 4 — Video recomposition | V01 hero hierarchy, V04 home, V06 Browse, V11 charts, V12 service context | Batches 1–2 | Purposeful landing, readable filter system, truthful badges, stable URLs |
| 5 — Freshness and failure | M12/M14/M15, V15/V17, S04 cleanup | Batches 2–4 | Cache invalidation matrix, partial-failure behavior, progress continuity, performance measurements |
| 6 — Recommendation quality | M06/M13, V09/V10; profile scope and provenance | Batches 2 and 5 | Ranking tests, source/identity diagnostics, improved reasons, sampled output review |
| 7 — Differentiation | V05 tonight flow, V13 details, V14 collection features; deeper music exploration | Core contracts stable | End-to-end tasks with realistic content and verified capabilities |
| 8 — Quality gate | M16/V18; responsive/accessibility/performance suite | All included feature batches | Final audit report with evidence, measured outcomes, remaining limitations |

### 12.1 Per-task completion template

For each task ID, Claude should record:

```md
### [ID] — [task name]
- Status: not started / in progress / complete / blocked
- Current problem reproduced: [steps or source proof]
- Behavior implemented: [exact user-visible result]
- Files changed: [paths]
- API/data contract changes: [none or explicit migration]
- Validation: [tests, runtime checks, screenshots]
- Failure states checked: [specific outcomes]
- Accessibility/mobile checked: [specific scenarios]
- Remaining limitations: [honest omissions]
```

“Polished UI,” “improved UX,” “works as expected,” and “tests pass” alone are not acceptable completion descriptions.

### 12.2 Scope guardrails

- Preserve existing working acquisition, playlist sync, service integrations, thumbnail caches, and resume/progress behavior.
- Do not ship a demo-only mock instead of the real interface.
- Do not invent backend capability, playback entitlement, recommendation confidence, release dates, or ownership completeness.
- Do not rename Save/Follow/Monitor without defining the behavioral mapping.
- Do not make visual fixes through unbounded CSS overrides that break shared Search/Detail pages.
- Do not equate animation, glass effects, or more panels with quality.
- Do not require new third-party infrastructure for the core P0/P1 fixes.
- Do not mass-reset user preferences or recommendation caches as a migration shortcut. Migrate scoped settings explicitly and preserve user intent.
- Do not retain stale migration comments that tell future implementers to recreate a known defect for parity.

## 13. Final release checklist

`[x]` done and verified. `[~]` the batch-1 slice is done, the named remainder
is not. `[ ]` not started.

### Music

- [ ] Opening view offers music to hear, not a dashboard of signals. — M01 recomposition, batch 3.
- [x] Hero actions and indicators never overlap at tested widths. — 320/390/768/1024/1440 offline, 390/1024/1440 live.
- [x] Play glyphs perform actual playback; row actions are wired. — card ▶, modal row ▶, and the modal's Play action all reach the player.
- [ ] Play/Saved/Monitoring/Acquiring/Syncing states are distinct and durable. — M09, batch 2.
- [~] Mix dialog is useful for listening and accessible by keyboard. — accessible (role, focus trap, Escape, focus restore, scroll lock) and verified live; the listening-first redesign is batch 3.
- [~] Taste tuning works by touch/keyboard and explains its actual effect. — native range with announced value and a line saying what it does; the pole labels still overpromise "deep cuts" (renaming touches Settings too, batch 3).
- [ ] New releases and missing collection content are separate and accurate. — M08.
- [ ] Artist/track/release identity survives normalization. — M13.
- [ ] Graphs and builder remain available through coherent Explore navigation. — M11.
- [ ] Back/reload/profile switch preserve the right context and state. — M10.
- [ ] Failed services, broken art, partial jobs, and empty profiles are handled deliberately. — M15.

### Video

- [ ] First viewport leads toward a watch/save decision and a personal rail. — V04, batch 4.
- [x] Mobile hero controls remain separate and usable. — dots in a reserved strip with 40px targets; verified live at 390.
- [ ] Available-now and Resume are based on actual capability/progress. — V07, batch 2.
- [~] Browse query, pagination, back, forward, and reload are deterministic. — the query race and the failed-page cursor are fixed; URL state, Back-to-shelf and reload restore are batch 4.
- [x] Old responses cannot contaminate a new result set. — generation guard, tested with deterministic out-of-order responses and a stale page 2.
- [~] Save/Hide/preferences have acknowledgment, rollback, and recovery. — Hide has all three plus Undo; preference saves and cache re-keying are batch 5.
- [ ] Cards use valid interactive structure and support keyboard/touch. — the music mix/album cards are done, but the video card is still an anchor with nested buttons (V16 remainder, batch 4).
- [ ] Preview/NEW/rank labels have been replaced or corrected to truthful semantics. — V07/V11.
- [ ] Provider and region context are clear; ownership is separate from entitlement. — V12.
- [ ] Personal taste is separated from household ownership. — V09.
- [ ] Monitoring and acquisition scope are explicit and use existing operational rules. — V08.
- [ ] Collection completion improves decisions without repeatedly reintroducing hidden titles. — V14.

### Both

- [ ] Shared design tokens, typography, spacing, iconography, and interaction states are coherent. — batch 3.
- [x] Fresh mobile and breakpoint transitions are tested; no sidebar obstruction. — desktop→mobile resize leaves the sidebar off-screen with the opener visible; drawer opens, Escape closes, focus returns. Verified live.
- [ ] Utility bubbles and persistent players do not cover content or dialogs. — S01 remainder.
- [ ] Reduced motion, text zoom, focus, contrast, and screen reader behavior are checked. — reduced motion and focus rings done where touched; no zoom, contrast or screen-reader pass yet.
- [ ] Performance budgets are measured on documented conditions. — §10, batch 5.
- [ ] Moderated user tasks meet the chosen release threshold. — M16/V18, batch 8.
- [x] Remaining backend/device limitations are explicitly documented. — DISCOVER_BIC_PROGRESS.md, per task.

## 14. Supporting standards and source notes

- Modal interaction requirements reference the [WAI-ARIA modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/): focus stays within the modal, Escape closes, and focus is managed on entry/exit. Follow the pattern through behavior, not merely by adding ARIA attributes.
- Taste control requirements reference the [WAI-ARIA slider pattern](https://www.w3.org/WAI/ARIA/apg/patterns/slider/). A native range input is preferable when it can meet the design without custom interaction risk.
- Focus clarity is also a useful design reference from [Apple's focus and selection guidance](https://developer.apple.com/design/human-interface-guidelines/focus-and-selection/). This document specifies a browser interface; it does not claim that adding arrow keys turns SoulSync into a native tvOS app.
- Video filter feasibility should be checked against the existing client and [TMDB movie Discover documentation](https://developer.themoviedb.org/reference/discover-movie). Frontend labels must express actual supported filter semantics.
- External references were checked during this review. Most detailed requirements above are original SoulSync-specific recommendations based on the reviewed source and local UI, not descriptions copied from competitor interfaces.

## 15. Definition of best in class for this work

A person should feel that SoulSync knows enough to help, explains enough to be trusted, and asks for very little effort. The interface should make beautiful media the subject; its control system should recede until needed. The next action should be obvious, its consequence accurate, and its result persistent.

The existing application has enough capability to justify that ambition. The work is to make that capability coherent. Finish the correctness and interaction foundations, then measure whether the redesigned pages help people find, enjoy, and collect media faster and with fewer mistakes. That is the standard this specification is intended to enforce.
