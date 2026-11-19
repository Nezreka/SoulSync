/**
 * The Connections tab (SETTINGS_PORT.md, step 10), part by part: the
 * metadata source, the Services tabs, tiles and modal, and the music side's
 * hidden API Configuration group with every music service card in it, and
 * the music media server, and MetaSync.
 */

import type { SettingsTabProps } from '../host';

import { MusicApiGroup } from './connections-extras';
import { MetadataSourceGroup } from './connections-metadata';
import { MetaSyncSection } from './connections-metasync';
import { ServerConnections } from './connections-servers';
import { ServicesPart } from './connections-services';
import { VideoApiGroup } from './connections-video-api';
import { VideoServerConnection } from './connections-video-server';
import { ConnectionsPage } from './connections/connections-page';
import { NotificationsSection } from './video-row-lists';

export function ConnectionsTab(props: SettingsTabProps) {
  const { store, part, opened } = props;
  // settings-bic: the tab's own page (pills, hero, server, services)
  if (part === 'page') return <ConnectionsPage {...props} />;
  if (part === 'metadata') return <MetadataSourceGroup store={store} />;
  if (part === 'services') return <ServicesPart opened={opened} />;
  if (part === 'api-music') return <MusicApiGroup store={store} />;
  if (part === 'api-video') return <VideoApiGroup />;
  if (part === 'video-server') return <VideoServerConnection />;
  if (part === 'notifications') return <NotificationsSection />;
  // Server Activity moved to System > Server Activity (settings-bic phase 3)
  if (part === 'servers') return <ServerConnections store={store} />;
  if (part === 'metasync') return <MetaSyncSection opened={opened} />;
  return null;
}
