/**
 * The page assembly. These tests cover the wiring the JSX decides — which is
 * where a page component can be wrong while every panel it mounts is right.
 *
 * AutoSyncModal and useAutoSync are stubbed on purpose. The real modal needs
 * loaded schedule state and a rendered board before an unschedule button
 * exists, and none of that would make the assertion sharper: the question is
 * only which function each action group's `onUnschedule` is bound to.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { LbCardData } from '../-sync.lb-tabs';
import type { SourceVertical } from '../-sync.use-vertical';
import type { AutoSyncModalProps } from './autosync-modal';
import type { SyncModalsProps } from './sync-modals';

let modalProps: AutoSyncModalProps | null = null;
/** Which vertical each panel was handed — the wiring a mutant can swap. */
const verticalsSeen: Record<string, SourceVertical> = {};
let syncModalsProps: SyncModalsProps | null = null;

/**
 * The four URL tabs and the two LB tabs are stubbed so the panel WIRING is
 * observable. Rendering the real ones would fetch and prove nothing about
 * which vertical or which card field the page passed.
 */
vi.mock('./url-import-tab', () => ({
  DeezerLinkTab: ({ vertical }: { vertical: SourceVertical }) => {
    verticalsSeen.deezerLink = vertical;
    return <div />;
  },
  YouTubeTab: ({ vertical }: { vertical: SourceVertical }) => {
    verticalsSeen.youtube = vertical;
    return <div />;
  },
  SpotifyPublicTab: () => <div />,
  ITunesLinkTab: () => <div />,
}));

vi.mock('./lb-sync-tab', () => ({
  ListenBrainzSyncTab: ({ onOpen }: { onOpen: (card: LbCardData) => void }) => (
    <button
      type="button"
      onClick={() => onOpen({ mbid: 'mb-1', title: 'Not The Id' } as LbCardData)}
    >
      lb-card
    </button>
  ),
  LbCardList: () => <div />,
}));

vi.mock('./sync-modals', () => ({
  SyncModals: (props: SyncModalsProps) => {
    syncModalsProps = props;
    return <div />;
  },
}));

vi.mock('./autosync-modal', () => ({
  AutoSyncModal: (props: AutoSyncModalProps) => {
    modalProps = props;
    return <div data-testid="auto-sync-modal" />;
  },
}));

const autoSyncStub = {
  state: { playlists: [], playlistSchedules: {}, weeklySchedules: {}, runHistory: [] },
  loading: false,
  loadError: null,
  now: 0,
  historyFilter: 'all',
  setHistoryFilter: vi.fn(),
  loadMoreHistory: vi.fn(),
  refresh: vi.fn(),
  saveHourly: vi.fn(),
  saveWeekly: vi.fn(),
  unscheduleHourly: vi.fn(),
  unscheduleWeekly: vi.fn(),
  runNow: vi.fn(),
  setOrganize: vi.fn(),
  bulkSchedule: vi.fn(),
  bulkUnschedule: vi.fn(),
  setDragging: vi.fn(),
};

vi.mock('../-sync.use-autosync', () => ({
  useAutoSync: () => autoSyncStub,
}));

import { SyncPage } from './sync-page';

beforeEach(() => {
  modalProps = null;
  for (const fn of Object.values(autoSyncStub)) {
    if (typeof fn === 'function' && 'mockClear' in fn) fn.mockClear();
  }
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));
  window.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.showToast;
  delete window.openMirroredPlaylistModal;
});

function openAutoSync() {
  fireEvent.click(screen.getByText('Auto-Sync'));
}

describe('the Auto-Sync action groups', () => {
  it('binds onUnschedule to DIFFERENT functions per group', () => {
    // The recorded trap. Three of the four members are identical between the
    // groups, so a copy-paste leaves both unschedules pointing at the hourly
    // one — and a weekly playlist then keeps refreshing while the UI says its
    // schedule is gone.
    render(<SyncPage />);
    openAutoSync();

    modalProps?.boardActions.onUnschedule(7);
    expect(autoSyncStub.unscheduleHourly).toHaveBeenCalledWith(7);
    expect(autoSyncStub.unscheduleWeekly).not.toHaveBeenCalled();

    modalProps?.weeklyActions.onUnschedule(9);
    expect(autoSyncStub.unscheduleWeekly).toHaveBeenCalledWith(9);
    // ...and the hourly one did not fire a second time.
    expect(autoSyncStub.unscheduleHourly).toHaveBeenCalledTimes(1);
  });

  it('sends each group its own extra member', () => {
    render(<SyncPage />);
    openAutoSync();

    modalProps?.boardActions.onDrop(3, 6);
    expect(autoSyncStub.saveHourly).toHaveBeenCalledWith(3, 6);

    const draft = { playlistId: 4, day: 1, hour: 2 };
    modalProps?.weeklyActions.onSave(draft as never);
    expect(autoSyncStub.saveWeekly).toHaveBeenCalledWith(draft);
    // The weekly path must not have gone through the hourly save.
    expect(autoSyncStub.saveHourly).toHaveBeenCalledTimes(1);
  });

  it('adapts onRunAgain, which carries a name runNow does not take', () => {
    render(<SyncPage />);
    openAutoSync();
    modalProps?.onRunAgain(12, 'Chill Mix');
    expect(autoSyncStub.runNow).toHaveBeenCalledWith(12);
  });

  it('opens the page\u2019s OWN mirrored modal for Details, not the legacy one', () => {
    // The React modal and the legacy openMirroredPlaylistModal are two
    // implementations over the same endpoint with different visuals, so the
    // same playlist looked different depending on which button you pressed.
    // The vanilla function stays alive for other PAGES; this page stops
    // calling it.
    const legacy = vi.fn();
    window.openMirroredPlaylistModal = legacy;
    render(<SyncPage />);
    openAutoSync();
    modalProps?.onOpenDetails(31);
    expect(legacy).not.toHaveBeenCalled();
  });
});

describe('the modal mount', () => {
  it('is absent until the header button is pressed, and closes back', () => {
    render(<SyncPage />);
    expect(screen.queryByTestId('auto-sync-modal')).toBeNull();
    openAutoSync();
    expect(screen.getByTestId('auto-sync-modal')).toBeTruthy();
    // onClose sets page state, so it needs act() — unlike the action-group
    // calls above, which land on stubs and change nothing React renders.
    act(() => modalProps?.onClose());
    expect(screen.queryByTestId('auto-sync-modal')).toBeNull();
  });
});

/**
 * Open Add playlist, paste a link, submit — the page's new way to reach a
 * paste-a-link tab now that the strip carries three chips instead of fifteen.
 */
function routeViaAddPlaylist(url: string): void {
  fireEvent.click(screen.getByText('+ Add playlist'));
  fireEvent.change(screen.getByLabelText('Paste a link'), { target: { value: url } });
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
}

describe('the panel map', () => {
  it('mounts under #sync-page with the server tab open', () => {
    // The id is load-bearing: the legacy chrome resolves a page by
    // `${pageId}-page`, and S4 deletes the vanilla node that owns it today.
    // (The list->compare swap needs a loaded list to emit onOpenCompare; it
    // belongs with the server tab's own tests, not here.)
    render(<SyncPage />);
    expect(document.querySelector('#sync-page')).toBeTruthy();
    expect(screen.getByText('Server Playlists')).toBeTruthy();
  });

  it('gives each source tab its OWN vertical', () => {
    // A swapped vertical is invisible in review and in every panel's own
    // tests: the Deezer link tab would render, fetch and look fine while
    // driving YouTube's state. Identity is the only thing that catches it.
    render(<SyncPage />);
    // These tabs are no longer chips in the strip — they are reached by
    // routing a link through Add playlist, which is the real path now.
    routeViaAddPlaylist('https://www.deezer.com/playlist/1');
    routeViaAddPlaylist('https://www.youtube.com/playlist?list=P1');
    expect(verticalsSeen.deezerLink).toBeTruthy();
    expect(verticalsSeen.youtube).toBeTruthy();
    expect(verticalsSeen.deezerLink).not.toBe(verticalsSeen.youtube);
  });

  it('opens a ListenBrainz card on its MBID, not its title', () => {
    // The card carries both, and only the mbid is the source id the vertical
    // prefixes downstream. The stub deliberately gives them different values.
    render(<SyncPage />);
    fireEvent.click(screen.getByText('+ Add playlist'));
    fireEvent.click(screen.getByRole('button', { name: /ListenBrainz/ }));
    fireEvent.click(screen.getByText('lb-card'));
    expect(syncModalsProps?.modals.openIdFor('listenbrainz')).toBe('mb-1');
  });

  it('mounts the Beatport pane', () => {
    // This was "leaves Beatport unmounted — the sub-shell is not built yet",
    // whose stated purpose was that filling the panel in would be a test change
    // rather than a silent one. It was not: it probed `.beatport-tab-content`,
    // a class the port did not emit at all, so it kept passing for two commits
    // after BeatportTab was wired in and only fired once that class was added.
    //
    // A negative assertion against a selector nothing renders passes for BOTH
    // reasons — the thing is absent, or the probe is wrong — and cannot tell
    // you which. So this asserts the pane by its ID, which the component has
    // always had.
    render(<SyncPage />);
    fireEvent.click(screen.getByText('Beatport'));
    expect(document.getElementById('beatport-rebuild-content')).not.toBeNull();
  });
});
