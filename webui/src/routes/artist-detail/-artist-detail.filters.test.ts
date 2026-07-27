import { describe, expect, it } from 'vitest';

import {
  applyMusicBrainzDeclutter,
  classifyReleaseContent,
  defaultFilterState,
  isMusicBrainzDiscography,
  isReleaseHidden,
  liveToggleLabel,
  NON_STUDIO_SECONDARY,
  releaseFlags,
  sectionCounts,
} from './-artist-detail.filters';

const mb = () => applyMusicBrainzDeclutter(defaultFilterState(), 'musicbrainz');

describe('defaultFilterState', () => {
  it('starts neutral — show everything', () => {
    // The declutter is applied LATER, once the discography's real source is
    // known; the initial state must not assume MusicBrainz.
    const s = defaultFilterState();
    expect(s.categories).toEqual({ albums: true, eps: true, singles: true });
    expect(s.content).toEqual({ live: true, compilations: true, featured: true });
    expect(s.ownership).toBe('all');
    expect(s.mbDeclutter).toBe(false);
  });
});

describe('applyMusicBrainzDeclutter', () => {
  it('hides non-studio for MusicBrainz only', () => {
    expect(mb().content.live).toBe(false);
    expect(mb().mbDeclutter).toBe(true);
  });

  it('leaves every other source untouched', () => {
    for (const source of ['spotify', 'deezer', 'itunes', '', null, undefined]) {
      const s = applyMusicBrainzDeclutter(defaultFilterState(), source);
      expect(s.content.live).toBe(true);
      expect(s.mbDeclutter).toBe(false);
    }
  });

  it('never hides compilations — they have their own toggle', () => {
    expect(mb().content.compilations).toBe(true);
  });

  it('matches the source case-insensitively', () => {
    expect(isMusicBrainzDiscography('MusicBrainz')).toBe(true);
  });
});

describe('liveToggleLabel', () => {
  it('renames the toggle honestly on MusicBrainz', () => {
    // It governs the broader non-studio set there, not just live albums.
    expect(liveToggleLabel(mb())).toBe('Non-Studio');
    expect(liveToggleLabel(defaultFilterState())).toBe('Live');
  });
});

describe('classifyReleaseContent — the shared title heuristic (#877)', () => {
  // Must stay identical to _classifyReleaseContent in library.js: the same
  // function classifies the Download Discography modal, and a release judged
  // differently in the two places is exactly the drift it exists to prevent.
  it('detects live by word, parens or brackets — not as a substring', () => {
    for (const title of ['Live at Leeds', 'Alive (Live)', 'Whatever [Live 1994]']) {
      expect(classifyReleaseContent({ title }).isLive).toBe(true);
    }
    // \b guards this: "Alive" and "Deliverance" contain "live" but are not live.
    for (const title of ['Alive', 'Deliverance', 'Olive']) {
      expect(classifyReleaseContent({ title }).isLive).toBe(false);
    }
  });

  it('detects compilations by album_type OR title phrase', () => {
    expect(classifyReleaseContent({ album_type: 'compilation', title: 'X' }).isCompilation).toBe(
      true,
    );
    for (const title of [
      'Greatest Hits',
      'The Best of Blur',
      'Anthology',
      'Essential',
      'The Collection',
    ]) {
      expect(classifyReleaseContent({ title }).isCompilation).toBe(true);
    }
    expect(classifyReleaseContent({ title: 'Kid A' }).isCompilation).toBe(false);
  });

  it('detects featured across feat. / ft. / featuring', () => {
    for (const title of ['Song (feat. Someone)', 'Song ft. Someone', 'Song featuring Someone']) {
      expect(classifyReleaseContent({ title }).isFeatured).toBe(true);
    }
    expect(classifyReleaseContent({ title: 'Feature Film' }).isFeatured).toBe(false);
  });

  it('reads `name` when there is no `title` — the download modal uses name', () => {
    expect(classifyReleaseContent({ name: 'Live at Wembley' }).isLive).toBe(true);
  });

  it('survives a release with neither title nor name', () => {
    expect(classifyReleaseContent({})).toEqual({
      isLive: false,
      isCompilation: false,
      isFeatured: false,
    });
  });
});

describe('releaseFlags', () => {
  it('uses the title heuristic off MusicBrainz', () => {
    const flags = releaseFlags({ title: 'Live at Leeds' }, false);
    expect(flags.isLive).toBe(true);
  });

  it('lets secondary_types OVERRIDE the live guess on MusicBrainz', () => {
    // The whole point: the title heuristic flags "Live Through This" as live,
    // and MB's secondary_types say it is a studio album.
    const flags = releaseFlags({ title: 'Live Through This', secondary_types: [] }, true);
    expect(flags.isLive).toBe(false);
  });

  it('keeps the title-derived compilation/featured flags on MusicBrainz', () => {
    // secondary_types only overrides LIVE; the other two still come from title.
    const flags = releaseFlags({ title: 'Greatest Hits (feat. X)', secondary_types: [] }, true);
    expect(flags.isCompilation).toBe(true);
    expect(flags.isFeatured).toBe(true);
  });

  it('catches soundtrack/remix/demo, which a title guess never would', () => {
    for (const type of ['Soundtrack', 'Remix', 'Demo', 'Live']) {
      expect(releaseFlags({ secondary_types: [type] }, true).isLive).toBe(true);
    }
  });

  it('ignores a secondary type that is not in the non-studio set', () => {
    // Compilation is deliberately excluded — it has its own toggle.
    expect(releaseFlags({ secondary_types: ['Compilation'] }, true).isLive).toBe(false);
    expect(NON_STUDIO_SECONDARY.has('compilation')).toBe(false);
  });

  it('survives a non-array secondary_types', () => {
    expect(releaseFlags({ secondary_types: 'live' as never }, true).isLive).toBe(false);
  });
});

describe('isReleaseHidden — content filters', () => {
  it('hides by each content flag when its toggle is off', () => {
    const s = defaultFilterState();
    s.content.live = false;
    expect(isReleaseHidden({}, { isLive: true, isCompilation: false, isFeatured: false }, s)).toBe(
      true,
    );

    const c = defaultFilterState();
    c.content.compilations = false;
    expect(isReleaseHidden({}, { isLive: false, isCompilation: true, isFeatured: false }, c)).toBe(
      true,
    );

    const f = defaultFilterState();
    f.content.featured = false;
    expect(isReleaseHidden({}, { isLive: false, isCompilation: false, isFeatured: true }, f)).toBe(
      true,
    );
  });

  it('never hides an OWNED release under the MB auto-declutter', () => {
    // The user did not choose that hide, so it must not bury their own music.
    const hidden = isReleaseHidden(
      { owned: true },
      { isLive: true, isCompilation: false, isFeatured: false },
      mb(),
    );
    expect(hidden).toBe(false);
  });

  it('DOES hide an owned release when the user turned the toggle off themselves', () => {
    // Off MusicBrainz there is no exemption — the toggle is user-driven, and
    // this keeps non-MB behaviour identical to before the exemption existed.
    const s = defaultFilterState();
    s.content.live = false;
    const hidden = isReleaseHidden(
      { owned: true },
      { isLive: true, isCompilation: false, isFeatured: false },
      s,
    );
    expect(hidden).toBe(true);
  });
});

describe('isReleaseHidden — ownership filter', () => {
  const plain = { isLive: false, isCompilation: false, isFeatured: false };

  it('filters to owned / missing', () => {
    const owned = { ...defaultFilterState(), ownership: 'owned' as const };
    expect(isReleaseHidden({ owned: true }, plain, owned)).toBe(false);
    expect(isReleaseHidden({ owned: false }, plain, owned)).toBe(true);

    const missing = { ...defaultFilterState(), ownership: 'missing' as const };
    expect(isReleaseHidden({ owned: false }, plain, missing)).toBe(false);
    expect(isReleaseHidden({ owned: true }, plain, missing)).toBe(true);
  });

  it('never hides a release whose ownership check is still running', () => {
    // owned === null means pending; the filter re-runs when the stream resolves.
    for (const ownership of ['owned', 'missing'] as const) {
      expect(isReleaseHidden({ owned: null }, plain, { ...defaultFilterState(), ownership })).toBe(
        false,
      );
    }
  });

  it('is not consulted at all once a content filter already hid the card', () => {
    const s = { ...defaultFilterState(), ownership: 'owned' as const };
    s.content.live = false;
    // owned:true would PASS the ownership filter, but live is off, so it stays hidden.
    expect(isReleaseHidden({ owned: true }, { ...plain, isLive: true }, s)).toBe(true);
  });
});

describe('sectionCounts', () => {
  it('counts only what is visible, so stats track the filtered view', () => {
    const s = { ...defaultFilterState(), ownership: 'owned' as const };
    const counts = sectionCounts([{ owned: true }, { owned: false }, { owned: true }], false, s);
    expect(counts).toEqual({ visible: 2, owned: 2, missing: 0 });
  });

  it('counts a pending release as visible but neither owned nor missing', () => {
    // So owned + missing does not necessarily equal visible.
    const counts = sectionCounts([{ owned: null }, { owned: true }], false, defaultFilterState());
    expect(counts).toEqual({ visible: 2, owned: 1, missing: 0 });
  });

  it('recomputes live-ness from secondary_types when the source is MusicBrainz', () => {
    const releases = [{ title: 'Live Through This', secondary_types: [] }];
    expect(sectionCounts(releases, true, mb()).visible).toBe(1);
    // ...and the same release IS hidden when the title guess is trusted.
    expect(sectionCounts(releases, false, { ...mb(), mbDeclutter: false }).visible).toBe(0);
  });
});
