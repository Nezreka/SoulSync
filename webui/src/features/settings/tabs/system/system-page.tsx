/**
 * The System tab (settings-bic DESIGN.md 3.1, 3.3): General, Security, API,
 * Maintenance, Backups, Server Activity, Developer and Logs, one pill each, each with a
 * line saying what it is for. The old tab's ten folded sections and the old
 * Logs tab live here now; `switchSettingsTab('logs')`, `'advanced'`,
 * `'system'` and `'server-activity'` still land on their pill (nav.ts).
 *
 * Every pill stays mounted while another is shown, as on the other rebuilt
 * tabs. Maintenance reads the database and the artwork cache each time its
 * pill is opened; the log viewer streams only while Logs is on screen.
 */

import type { SettingsTabProps } from '../../host';

import { useNav } from '../../nav';
import { usePageTabs } from '../../page-tabs';
import { Note } from '../../ui';
import s from '../../ui/settings.module.css';
import { ViewPanel } from '../downloads/downloads-page';
import { LogsTab } from '../logs-tab';
import { ServerActivitySettingsCard } from '../server-activity-settings';
import { SecurityView } from '../system-security';
import { ApiView, DeveloperView, GeneralView, MaintenanceView, useLogLevel } from '../system-tab';
import { BackupsView } from './backups-view';

const TAB = 'advanced';

export function SystemPage({ store, opened }: SettingsTabProps) {
  const state = useNav();
  const pages = usePageTabs();
  const here = state.tab === TAB;
  const view = here ? state.view : 'general';
  // one copy of the level: General changes it, Logs reads it back
  const log = useLogLevel(store);

  return (
    <div data-stg-new={TAB} hidden={!here}>
      <ViewPanel tab={TAB} view="general" open={view === 'general'}>
        <GeneralView store={store} log={log} />
      </ViewPanel>
      <ViewPanel tab={TAB} view="security" open={view === 'security'}>
        <SecurityView store={store} />
      </ViewPanel>
      <ViewPanel tab={TAB} view="api" open={view === 'api'}>
        <ApiView store={store} />
      </ViewPanel>
      <ViewPanel tab={TAB} view="maintenance" open={view === 'maintenance'}>
        <MaintenanceView store={store} shown={here && view === 'maintenance'} opened={opened} />
      </ViewPanel>
      <ViewPanel tab={TAB} view="backups" open={view === 'backups'}>
        <BackupsView shown={here && view === 'backups'} opened={opened} />
      </ViewPanel>
      <ViewPanel tab={TAB} view="activity" open={view === 'activity'}>
        <ServerActivitySettingsCard
          denied={<Note info>Only an admin can change Server Activity.</Note>}
        />
      </ViewPanel>
      <ViewPanel tab={TAB} view="developer" open={view === 'developer'}>
        <DeveloperView store={store} />
      </ViewPanel>
      <ViewPanel tab={TAB} view="logs" open={view === 'logs'}>
        <div className={s.cards}>
          <LogsTab store={store} opened={pages.opened.logs ?? 0} active={pages.tab === 'logs'} />
        </div>
      </ViewPanel>
    </div>
  );
}

/** the System tab: one page with its pills (settings-bic phase 6) */
export function SystemTab(props: SettingsTabProps) {
  if (props.part === 'page') return <SystemPage {...props} />;
  return null;
}
