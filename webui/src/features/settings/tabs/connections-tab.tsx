/**
 * The Connections tab (SETTINGS_PORT.md, step 10), part by part: the
 * metadata source, the Services tabs, tiles and modal, and the music side's
 * hidden API Configuration group with every music service card in it.
 */

import type { SettingsTabProps } from '../host';

import { MusicApiGroup } from './connections-extras';
import { MetadataSourceGroup } from './connections-metadata';
import { ServicesPart } from './connections-services';

export function ConnectionsTab({ store, part, opened }: SettingsTabProps) {
  if (part === 'metadata') return <MetadataSourceGroup store={store} />;
  if (part === 'services') return <ServicesPart opened={opened} />;
  if (part === 'api-music') return <MusicApiGroup store={store} />;
  return null;
}
