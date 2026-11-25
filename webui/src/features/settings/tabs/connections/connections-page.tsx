/**
 * The Connections tab (settings-bic DESIGN.md 3.1, mockup 01): Servers and
 * services, MetaSync (music) and Notifications (video), one pill each.
 *
 * Servers and services reads the tab back in one sentence, then: the media
 * server as a pick-one row with its state in words (View configuration opens
 * that server's panel in the side sheet), the metadata source, and the
 * services as tiles whose status is what SoulSync knows (serviceStatus), each
 * opening its card in the side sheet.
 *
 * Every pill stays mounted while another is shown, as on Downloads, so the
 * cards, pickers and an edit not saved yet are all still there.
 */

import { useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../../host';

import { useNav } from '../../nav';
import { Btn, SectionLabel, Sheet, SheetFootSay, StatusWords } from '../../ui';
import s from '../../ui/settings.module.css';
import { statusFrames } from '../connections';
import { MetadataSourceGroup } from '../connections-metadata';
import { MetaSyncSection } from '../connections-metasync';
import {
  SERVER_LOOK,
  type Server,
  ServerConnections,
  serverName,
  serverState,
  testConnection,
  useTestResults,
} from '../connections-servers';
import { ServicesPart, servicesModel } from '../connections-services';
import {
  VideoServerConnection,
  VideoServerFields,
  useVideoServerState,
  videoServerName,
} from '../connections-video-server';
import { ViewPanel } from '../downloads/downloads-page';
import { NotificationsCard } from '../video-row-lists';
import { useVideoServer, videoServer } from '../video-server';
import { markVideoShown, useVideoShown } from '../video-shown';

const TAB = 'connections';

function useSide(): 'music' | 'video' {
  return useSyncExternalStore(
    (listener) => {
      const observer = new MutationObserver(listener);
      observer.observe(document.body, { attributes: true, attributeFilter: ['data-side'] });
      return () => observer.disconnect();
    },
    () => (document.body.getAttribute('data-side') === 'video' ? 'video' : 'music'),
    () => 'music',
  );
}

export function ConnectionsPage(props: SettingsTabProps) {
  const { store, opened } = props;
  const state = useNav();
  const side = useSide();
  const here = state.tab === TAB;
  const view = here ? state.view : 'services';
  const settings = useSyncExternalStore(store.subscribe, () => store.getState().settings);
  const frame = useSyncExternalStore(statusFrames.subscribe, statusFrames.get);
  const tests = useTestResults();
  const [viewing, setViewing] = useState<Server | null>(null);
  const [videoSheet, setVideoSheet] = useState(false);
  const [host, setHost] = useState<HTMLElement | null>(null);

  // the music side never showed the video settings page: load the
  // connections the first time their pill opens (as Import lists does)
  const shown = useVideoShown();
  const notify = here && view === 'notifications';
  useEffect(() => {
    if (notify && side !== 'video' && shown === 0) markVideoShown();
  }, [notify, side, shown]);

  // the server panels live in the server sheet's body (hidden while closed)
  useEffect(() => setHost(document.getElementById('server-sheet-body')), []);

  const saved = (settings as Record<string, unknown>).active_media_server;
  const server = typeof saved === 'string' && saved ? saved : 'plex';
  const video = useVideoServer();
  const videoState = useVideoServerState();
  // the sheet's answer: its own Test, else Test server's while it is the active one
  const viewingNow = viewing
    ? serverState(
        viewing,
        frame as never,
        viewing === server ? (tests[viewing] ?? tests.server) : tests[viewing],
      )
    : null;
  const sheetSaid = viewing
    ? (tests[viewing] ?? (viewing === server ? tests.server : undefined))
    : undefined;

  const close = () => {
    setViewing(null);
    const w = window as Window & { _settingsAutosaveOff?: boolean };
    if (w._settingsAutosaveOff !== true && store.hasPending()) void store.flush();
  };

  return (
    <div data-stg-new={TAB} hidden={!here}>
      <ViewPanel tab={TAB} view="services" open={view === 'services'}>
        <div hidden={side === 'video'} data-media-server="music">
          <SectionLabel title="Media server" hint="Where your library is played from" />
          <ServerConnections store={store} host={host} onView={(s) => setViewing(s)} />
        </div>
        <div hidden={side !== 'video'} data-media-server="video">
          <SectionLabel title="Media server" hint="The server your movies and shows play from" />
          <VideoServerConnection onView={() => setVideoSheet(true)} />
          <Sheet
            open={videoSheet && side === 'video'}
            onClose={() => setVideoSheet(false)}
            id="video-server-sheet"
            bodyClass="src-modal-open"
            title={videoServerName(video.active)}
            titleId="video-server-sheet-title"
            sub="The video side's media server"
            mark={
              video.active ? (
                <img src={SERVER_LOOK[video.active].logo} alt="" width={40} height={40} />
              ) : null
            }
            status={<StatusWords tone={videoState.tone}>{videoState.text}</StatusWords>}
            foot={
              <>
                <Btn
                  icon="refresh"
                  data-video-conn-test={video.active ?? ''}
                  disabled={!video.active || video.active === 'soulsync'}
                  onClick={() =>
                    video.active &&
                    video.active !== 'soulsync' &&
                    void videoServer.test(video.active)
                  }
                >
                  Test connection
                </Btn>
                <SheetFootSay>
                  <StatusWords
                    tone={video.active ? video.said[video.active]?.tone : undefined}
                    live
                  >
                    {(video.active && video.said[video.active]?.text) || ''}
                  </StatusWords>
                </SheetFootSay>
                <Btn kind="primary" onClick={() => setVideoSheet(false)}>
                  Done
                </Btn>
              </>
            }
          >
            <VideoServerFields />
          </Sheet>
        </div>

        <div hidden={side === 'video'}>
          <MetadataSourceGroup store={store} />
        </div>

        <ServicesPart model={servicesModel} opened={opened} />

        <Sheet
          open={viewing !== null}
          onClose={close}
          id="server-config-sheet"
          title={viewing ? serverName(viewing) : 'Media server'}
          titleId="server-sheet-title"
          sub="Media server"
          mark={
            viewing ? <img src={SERVER_LOOK[viewing].logo} alt="" width={40} height={40} /> : null
          }
          status={
            viewingNow ? <StatusWords tone={viewingNow.tone}>{viewingNow.text}</StatusWords> : null
          }
          bodyId="server-sheet-body"
          bodyClass="src-modal-open"
          foot={
            <>
              {viewing ? (
                <Btn
                  icon="refresh"
                  id="server-sheet-test"
                  onClick={() => void testConnection(viewing, store)}
                >
                  {viewing === 'soulsync' ? 'Verify music library folder' : 'Test connection'}
                </Btn>
              ) : null}
              <SheetFootSay>
                <StatusWords id="server-sheet-result" tone={sheetSaid?.tone} live>
                  {sheetSaid?.text || ''}
                </StatusWords>
              </SheetFootSay>
              <Btn kind="primary" onClick={close}>
                Done
              </Btn>
            </>
          }
        />
      </ViewPanel>

      <ViewPanel tab={TAB} view="metasync" open={view === 'metasync'}>
        <MetaSyncSection opened={opened} />
      </ViewPanel>

      <ViewPanel tab={TAB} view="notifications" open={view === 'notifications'}>
        <div className={s.cards}>
          <NotificationsCard />
        </div>
      </ViewPanel>
    </div>
  );
}
