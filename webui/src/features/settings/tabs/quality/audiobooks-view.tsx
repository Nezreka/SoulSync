/**
 * Quality > Audiobooks (settings-bic phase 3; it was "Audiobook Quality" on
 * Library > Organization > Audiobooks): what counts as a good audiobook file.
 * One profile for every book. The keys are audiobooks.quality.*, saved
 * through the store as they were.
 */

import { useEffect, useState, useSyncExternalStore } from 'react';

import type { SettingsStore } from '../../settings-store';

import { Card, CheckChips, NumberField, Row, SectionLabel, Select, Switch } from '../../ui';
import { int, useLoadedDraft } from '../library-card';
import { text } from '../source-panels';

type Section = Record<string, unknown>;

export const AB_FORMATS: Array<[string, string]> = [
  ['m4b', 'M4B'],
  ['m4a', 'M4A'],
  ['mp3', 'MP3'],
  ['opus', 'Opus'],
  ['ogg', 'OGG'],
  ['flac', 'FLAC'],
];
export const AB_LAYOUTS: Array<[string, string]> = [
  ['single', 'Single file (whole book)'],
  ['multiple', 'Multiple files (chapters or parts)'],
];

/** the format and layout switches as loadSettingsData set them: an empty
 *  allowed list is every format; "any" layout is both switches */
export function qualitySwitches(quality: Section) {
  const allowed = Array.isArray(quality.allowed_formats)
    ? (quality.allowed_formats as unknown[]).map((f) => text(f))
    : [];
  const layout = text(quality.file_layout) || 'any';
  return {
    formats: AB_FORMATS.map(([v]) => v).filter((v) => !allowed.length || allowed.includes(v)),
    layouts: AB_LAYOUTS.map(([v]) => v).filter((v) => layout === 'any' || layout === v),
  };
}

export function AudiobookQualityView({ store }: { store: SettingsStore }) {
  const settings = useSyncExternalStore(store.subscribe, () => store.getState().settings);
  const loads = useSyncExternalStore(store.subscribe, () => store.getState().loads);
  const ab = (settings.audiobooks || {}) as Section;
  const quality = (ab.quality || {}) as Section;
  const set = (key: string, value: unknown) => store.set('audiobooks', key, value);

  const order =
    Array.isArray(quality.format_order) && quality.format_order.length
      ? (quality.format_order as unknown[]).map((f) => text(f))
      : AB_FORMATS.map(([v]) => v);

  // the switches keep what was clicked (every one off is allowed, and means
  // all), and save what it means; a load puts the saved state back
  const [switches, setSwitches] = useState(() => qualitySwitches(quality));
  useEffect(() => setSwitches(qualitySwitches(quality)), [loads]); // eslint-disable-line react-hooks/exhaustive-deps
  function toggleFormat(value: string, checked: boolean) {
    const formats = AB_FORMATS.map(([v]) => v).filter((v) =>
      v === value ? checked : switches.formats.includes(v),
    );
    setSwitches({ ...switches, formats });
    // every format on is stored as [] ("no restriction")
    set('quality.allowed_formats', formats.length === AB_FORMATS.length ? [] : formats);
  }
  function toggleLayout(value: string, checked: boolean) {
    const layouts = AB_LAYOUTS.map(([v]) => v).filter((v) =>
      v === value ? checked : switches.layouts.includes(v),
    );
    setSwitches({ ...switches, layouts });
    // exactly one on picks it; both on, or both off, means either
    set('quality.file_layout', layouts.length === 1 ? layouts[0] : 'any');
  }

  const minRate = useLoadedDraft(store, text(quality.min_bitrate_kbps ?? 0), (raw) =>
    set('quality.min_bitrate_kbps', Math.max(0, int(raw, 0))),
  );
  const maxRate = useLoadedDraft(store, text(quality.max_bitrate_kbps ?? 0), (raw) =>
    set('quality.max_bitrate_kbps', Math.max(0, int(raw, 0))),
  );

  return (
    <div data-audiobook-quality="">
      <SectionLabel
        title="Formats"
        hint="One profile for every audiobook. The per-author card on Library holds what differs by author."
      />
      <Card id="audiobook-quality-card">
        <Row
          label="Preferred format"
          htmlFor="audiobook-format-first"
          field
          help="Ranked first when several releases are otherwise equal; the rest keep their usual order behind it."
          more={
            <>
              <strong>M4B</strong> is the only format built for audiobooks: chapters, bookmarks, and
              one file instead of ninety. Pick <strong>MP3</strong> if your player cannot read M4B.
              Format never outweighs having the right book and the right narrator.
            </>
          }
        >
          <Select
            id="audiobook-format-first"
            value={order[0] || 'm4b'}
            onChange={(event) => {
              // the chosen format leads; the default order follows behind it
              const first = event.target.value;
              set('quality.format_order', [
                first,
                ...AB_FORMATS.map(([v]) => v).filter((f) => f !== first),
              ]);
            }}
            options={AB_FORMATS.map(([v, label]) => ({
              value: v,
              label: v === 'm4b' ? 'M4B (recommended)' : label,
            }))}
          />
        </Row>
        <Row
          label="Allowed formats"
          stack
          help="A format switched off is never shown or downloaded."
          more="Preferred format only ranks, so with M4B preferred an MP3 is still grabbed when no M4B turns up; switch MP3 off here to wait for an M4B instead. A release whose format cannot be told from its title is still allowed. Switching every format off is the same as allowing them all."
        >
          <CheckChips
            id="audiobook-allowed-formats"
            label="Allowed formats"
            options={AB_FORMATS.map(([v, label]) => ({ value: v, label }))}
            value={loads ? switches.formats : []}
            onToggle={toggleFormat}
          />
        </Row>
        <Row
          label="File layout"
          stack
          help="Whether a book should arrive as one file or as several smaller files, usually one per chapter."
          more="Switch one off and releases laid out that way are skipped; leave both on (or both off) for either. Only Soulseek says how many files a release has before it is downloaded; torrents and usenet do not, so they are always allowed."
        >
          <CheckChips
            id="audiobook-file-layout"
            label="File layout"
            options={AB_LAYOUTS.map(([v, label]) => ({ value: v, label }))}
            value={loads ? switches.layouts : []}
            onToggle={toggleLayout}
          />
        </Row>
      </Card>

      <SectionLabel title="Bitrate" hint="0 means no opinion" />
      <Card>
        <Row
          label="Reject below"
          htmlFor="audiobook-min-bitrate"
          field
          help="Hides releases below this bitrate. This is taste, separate from the completeness check on Library."
          more="That one asks whether a release could be the whole book at all, this one asks whether you want it. Audible's own files are 32 kbps mono and 64 kbps stereo, so 32 is a reasonable floor and anything above 128 is more than speech needs."
        >
          <NumberField
            id="audiobook-min-bitrate"
            min="0"
            max="512"
            placeholder="0"
            unit="kbps"
            step="1"
            value={loads ? minRate.value : ''}
            onChange={(event) => minRate.onChange(event.target.value)}
          />
        </Row>
        <Row
          label="Reject above"
          htmlFor="audiobook-max-bitrate"
          field
          help="Hides releases above this bitrate, for when disk space matters more than headroom."
          more="Speech gains nothing audible above about 128 kbps, so a 45-hour book at 240 kbps is roughly 4GB for no benefit."
        >
          <NumberField
            id="audiobook-max-bitrate"
            min="0"
            max="2000"
            placeholder="0"
            unit="kbps"
            step="1"
            value={loads ? maxRate.value : ''}
            onChange={(event) => maxRate.onChange(event.target.value)}
          />
        </Row>
      </Card>

      <SectionLabel title="Productions" />
      <Card>
        <Row
          label="Show dramatised adaptations"
          htmlFor="audiobook-allow-dramatized"
          help="GraphicAudio and similar full-cast productions with music and sound effects."
          more="They are a different product from the audiobook: different cast, different running time, usually sold in several separately downloaded parts. Left on they are shown but ranked below real readings and clearly labelled; turned off they are hidden entirely."
        >
          <Switch
            id="audiobook-allow-dramatized"
            checked={quality.allow_dramatized !== false}
            onChange={(event) => set('quality.allow_dramatized', event.target.checked)}
          />
        </Row>
      </Card>
    </div>
  );
}
