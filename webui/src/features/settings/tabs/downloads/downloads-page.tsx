/**
 * The Downloads tab (settings-bic DESIGN.md 3.1): Sources, Behaviour and
 * Filtering, one pill each. Every pill stays mounted while another is shown,
 * so an edit that has not saved yet is still there on the way back, and the
 * source panels stay in the page for the sheet to show.
 */

import { type ReactNode, useEffect, useRef, useSyncExternalStore } from 'react';

import type { SettingsTabProps } from '../../host';

import { nav, useNav } from '../../nav';
import { KindBar, KindSay, Segmented } from '../../ui';
import s from '../../ui/settings.module.css';
import { CHAIN_KINDS, type SourcesModel, sourcesModel } from '../sources-model';
import { MusicBehaviour, VideoBehaviour } from './behaviour-view';
import { FilteringView } from './filtering-view';
import { SourcePanelsHome, SourceSheet, SourcesView } from './sources-view';

const TAB = 'downloads';

/** one pill's page: in the document always, on screen when it is the open one */
export function ViewPanel({
  tab,
  view,
  open,
  children,
}: {
  tab: string;
  view: string;
  open: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={s.view}
      role="tabpanel"
      id={`stg-view-${tab}-${view}`}
      aria-labelledby={`stg-pill-${tab}-${view}`}
      data-stg-tab={tab}
      data-stg-view={view}
      data-ui=""
      hidden={!open}
    >
      {children}
    </div>
  );
}

const side = () => (document.body.getAttribute('data-side') === 'video' ? 'video' : 'music');

/** the address and the sources model say the same kind and the same open
 *  sheet, whichever of them changed (a link, Back, a click, an old caller) */
function useAddressSync(model: SourcesModel) {
  const state = useNav();
  const kind = useSyncExternalStore(model.subscribe, () => model.getState().kind);
  const open = useSyncExternalStore(model.subscribe, () => model.getState().open);
  const onSources = state.tab === TAB && state.view === 'sources';
  // the first pass only hears the address: nothing has been clicked yet, and
  // the address may have moved on since this render
  const fromAddress = useRef({ kind: true, open: true });

  useEffect(() => {
    if (!onSources || !state.kind || !(state.kind in CHAIN_KINDS)) return;
    // a kind the address names is a pick: it outranks the side's own default
    const now = model.getState();
    if (state.kind === now.kind && now.kindChosen) return;
    fromAddress.current.kind = true;
    model.switchChain(state.kind);
  }, [state.kind, onSources]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (fromAddress.current.kind) {
      fromAddress.current.kind = false;
      return;
    }
    const now = model.getState();
    const at = nav.get();
    if (at.tab === TAB && at.view === 'sources' && now.kindChosen && now.kind !== at.kind)
      nav.patch({ kind: now.kind });
  }, [kind, onSources]); // eslint-disable-line react-hooks/exhaustive-deps

  // what the address said last time, so a tab change is not read as a close
  const addressOpen = useRef(state.open);
  useEffect(() => {
    const was = addressOpen.current;
    addressOpen.current = state.open;
    const now = model.getState().open;
    if (state.open === now) return;
    // opened from somewhere else (window.openSourceModal) as the tab arrived:
    // the address had no sheet before either, so the sheet stays and the
    // address follows it (the effect below)
    if (state.tab === TAB && !state.open && !was && now) return;
    // the sheet belongs to this tab: leaving the tab closes it
    if (state.tab !== TAB || !state.open) {
      if (now) {
        fromAddress.current.open = true;
        model.closeModal();
      }
      return;
    }
    model.openModal(state.open);
    // the model follows unless it is not a source with settings
    fromAddress.current.open = model.getState().open !== now;
    // not a source with settings: the address says so
    if (model.getState().open !== state.open) nav.patch({ open: model.getState().open });
  }, [state.open, state.tab]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (fromAddress.current.open) {
      fromAddress.current.open = false;
      return;
    }
    const at = nav.get();
    if (open === at.open) return;
    // opened from somewhere else on the page (window.openSourceModal): the
    // sheet lives on Downloads > Sources
    if (open && at.tab !== TAB) nav.go(TAB, 'sources', { open });
    else nav.patch({ open });
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
}

export function DownloadsPage(props: SettingsTabProps & { model?: SourcesModel }) {
  const { store, model = sourcesModel } = props;
  const state = useNav();
  useAddressSync(model);
  const here = state.tab === TAB;
  const view = here ? state.view : 'sources';
  // Behaviour's kind: the address when it names one, the side otherwise
  const kind =
    here && view === 'behaviour' && (state.kind === 'music' || state.kind === 'video')
      ? state.kind
      : side();

  return (
    <div data-stg-new={TAB} hidden={!here}>
      <ViewPanel tab={TAB} view="sources" open={view === 'sources'}>
        <SourcesView {...props} model={model} active={props.active && view === 'sources'} />
      </ViewPanel>

      <ViewPanel tab={TAB} view="behaviour" open={view === 'behaviour'}>
        <KindBar>
          <Segmented
            id="stg-behaviour-kind"
            label="Which downloads these settings are for"
            value={kind}
            options={[
              { value: 'music', label: 'Music' },
              { value: 'video', label: 'Video' },
            ]}
            panel={(value) => `stg-behaviour-${value}`}
            onChange={(next) => nav.patch({ kind: next })}
          />
          <KindSay>
            {kind === 'music'
              ? 'How music downloads run. These save with the page.'
              : 'How movie and TV downloads run. These save at once.'}
          </KindSay>
        </KindBar>
        <div id="stg-behaviour-music" role="tabpanel" aria-label="Music" hidden={kind !== 'music'}>
          <div className={s.cards}>
            <MusicBehaviour store={store} />
          </div>
        </div>
        <div id="stg-behaviour-video" role="tabpanel" aria-label="Video" hidden={kind !== 'video'}>
          <div className={s.cards}>
            <VideoBehaviour />
          </div>
        </div>
      </ViewPanel>

      <ViewPanel tab={TAB} view="filtering" open={view === 'filtering'}>
        <div className={s.cards}>
          <FilteringView store={store} />
        </div>
      </ViewPanel>

      <SourceSheet store={store} model={model} />
      <SourcePanelsHome {...props} />
    </div>
  );
}
