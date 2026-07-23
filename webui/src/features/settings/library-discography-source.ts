export const LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS = [
  { value: 'primary', label: 'Use primary metadata source' },
  { value: 'automatic', label: 'Automatic commercial catalogue' },
  { value: 'itunes', label: 'iTunes' },
  { value: 'deezer', label: 'Deezer' },
  { value: 'musicbrainz', label: 'MusicBrainz' },
  { value: 'spotify', label: 'Spotify' },
] as const;

export type LibraryDiscographySource =
  (typeof LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS)[number]['value'];

type SettingsPayload = {
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
};

type SoulSyncWindow = Window & {
  _settingsPayload?: SettingsPayload;
  showToast?: (message: string, type?: string) => void;
};

const SELECT_ID = 'library-discography-source';
const GROUP_ID = 'library-discography-source-group';
const VALID_SOURCES = new Set<string>(
  LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS.map((option) => option.value),
);

export function normalizeLibraryDiscographySource(
  value: unknown,
): LibraryDiscographySource {
  const normalized = String(value ?? '').trim().toLowerCase();
  return VALID_SOURCES.has(normalized)
    ? (normalized as LibraryDiscographySource)
    : 'primary';
}

async function loadSettings(fetchImpl: typeof fetch): Promise<SettingsPayload> {
  const response = await fetchImpl('/api/settings');
  if (!response.ok) {
    throw new Error(`Settings request failed with HTTP ${response.status}`);
  }

  const settings = (await response.json()) as SettingsPayload;
  if (!settings || typeof settings !== 'object') {
    throw new Error('Settings response is invalid');
  }

  return settings;
}

async function saveLibraryDiscographySource(
  source: LibraryDiscographySource,
  fetchImpl: typeof fetch,
  targetWindow: SoulSyncWindow,
): Promise<void> {
  const settings = await loadSettings(fetchImpl);
  settings.metadata = {
    ...(settings.metadata ?? {}),
    library_discography_source: source,
  };

  const response = await fetchImpl('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });

  const result = (await response.json()) as { success?: boolean; error?: string };
  if (!response.ok || result.success !== true) {
    throw new Error(result.error || `Settings save failed with HTTP ${response.status}`);
  }

  targetWindow._settingsPayload = settings;
}

function createSelector(documentRef: Document): HTMLSelectElement {
  const select = documentRef.createElement('select');
  select.id = SELECT_ID;
  select.className = 'form-select';

  for (const optionDefinition of LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS) {
    const option = documentRef.createElement('option');
    option.value = optionDefinition.value;
    option.textContent = optionDefinition.label;
    select.append(option);
  }

  return select;
}

function insertSelector(documentRef: Document): HTMLSelectElement | null {
  const existing = documentRef.getElementById(SELECT_ID);
  if (existing instanceof HTMLSelectElement) return existing;

  const primarySelect = documentRef.getElementById('metadata-fallback-source');
  if (!(primarySelect instanceof HTMLSelectElement)) return null;

  const primaryGroup = primarySelect.closest('.form-group') ?? primarySelect.parentElement;
  if (!primaryGroup?.parentElement) return null;

  const group = documentRef.createElement('div');
  group.id = GROUP_ID;
  group.className = 'form-group';

  const label = documentRef.createElement('label');
  label.htmlFor = SELECT_ID;
  label.textContent = 'Library discography source:';

  const select = createSelector(documentRef);

  const hint = documentRef.createElement('small');
  hint.className = 'settings-hint';
  hint.textContent =
    'Controls album, EP and single catalogues shown in Library artist views. Automatic tries iTunes, then Deezer.';

  group.append(label, select, hint);
  primaryGroup.insertAdjacentElement('afterend', group);
  return select;
}

export async function mountLibraryDiscographySourceSelector({
  documentRef = document,
  targetWindow = window as SoulSyncWindow,
  fetchImpl = fetch,
}: {
  documentRef?: Document;
  targetWindow?: SoulSyncWindow;
  fetchImpl?: typeof fetch;
} = {}): Promise<HTMLSelectElement | null> {
  const select = insertSelector(documentRef);
  if (!select) return null;

  try {
    const settings = targetWindow._settingsPayload ?? (await loadSettings(fetchImpl));
    targetWindow._settingsPayload = settings;
    select.value = normalizeLibraryDiscographySource(
      settings.metadata?.library_discography_source,
    );
  } catch (error) {
    console.error('Failed to load Library discography source:', error);
    select.value = 'primary';
  }

  if (select.dataset.libraryDiscographyBound === 'true') return select;
  select.dataset.libraryDiscographyBound = 'true';

  select.addEventListener('change', async () => {
    const source = normalizeLibraryDiscographySource(select.value);
    select.disabled = true;

    try {
      await saveLibraryDiscographySource(source, fetchImpl, targetWindow);
      targetWindow.showToast?.('Library discography source saved', 'success');
    } catch (error) {
      console.error('Failed to save Library discography source:', error);
      targetWindow.showToast?.(
        error instanceof Error ? error.message : 'Failed to save Library discography source',
        'error',
      );
    } finally {
      select.disabled = false;
    }
  });

  return select;
}
