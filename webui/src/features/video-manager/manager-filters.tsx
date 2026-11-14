/** the manager's filter panel, and the chips that say which filters are on. */

import type { Counted, Facets, Filters, Side, ViewMode } from './types';

import { NEED_COPY, codecName, formatSize, languageName } from './format';
import { EMPTY_FILTERS } from './types';
import s from './video-manager.module.css';

function Pick({
  label,
  value,
  onChange,
  options,
  any = 'Any',
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  any?: string;
}) {
  if (!options.length) return null;
  return (
    <label className={s.field}>
      <span className={s.fieldLabel}>{label}</span>
      <select className={s.select} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{any}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

const withCount = (items: Counted[], name: (v: string) => string = (v) => v) =>
  items.map((x) => ({ value: x.value, label: `${name(x.value)} (${x.count.toLocaleString()})` }));

export function FilterPanel({
  side,
  view,
  facets,
  filters,
  onChange,
  onClose,
}: {
  side: Side;
  view: ViewMode;
  facets: Facets;
  filters: Filters;
  onChange: (next: Filters) => void;
  onClose: () => void;
}) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const titlesOnly = view !== 'files';
  return (
    <section className={s.panel} aria-label="Filters">
      <div className={s.panelGrid}>
        <Pick
          label="Quality"
          value={filters.resolution}
          onChange={(resolution) => set({ resolution })}
          options={withCount(facets.resolutions)}
        />
        <Pick
          label="Video codec"
          value={filters.codec}
          onChange={(codec) => set({ codec })}
          options={withCount(facets.codecs, codecName)}
        />
        <Pick
          label="HDR"
          value={filters.hdr}
          onChange={(hdr) => set({ hdr: hdr as Filters['hdr'] })}
          options={[
            { value: '1', label: 'HDR or Dolby Vision' },
            { value: '0', label: 'SDR only' },
          ]}
        />
        <Pick
          label="Audio language"
          value={filters.audio_language}
          onChange={(audio_language) => set({ audio_language })}
          options={withCount(facets.audio_languages, languageName)}
        />
        <Pick
          label="Subtitle language"
          value={filters.subtitle_language}
          onChange={(subtitle_language) => set({ subtitle_language })}
          options={withCount(facets.subtitle_languages, languageName)}
        />
        <Pick
          label="Drive"
          value={filters.drive}
          onChange={(drive) => set({ drive })}
          options={facets.drives.map((d) => ({
            value: d.value,
            label: `${d.value} (${formatSize(d.size_bytes) || d.count.toLocaleString()})`,
          }))}
        />
        {facets.libraries.length > 1 ? (
          <Pick
            label="Library"
            value={filters.video_library}
            onChange={(video_library) => set({ video_library })}
            options={facets.libraries.map((l) => ({ value: String(l.id), label: l.name }))}
          />
        ) : null}
        {titlesOnly ? (
          <>
            <Pick
              label="Genre"
              value={filters.genre}
              onChange={(genre) => set({ genre })}
              options={withCount(facets.genres)}
            />
            <Pick
              label="In library"
              value={filters.status}
              onChange={(status) => set({ status: status as Filters['status'] })}
              options={[
                { value: 'owned', label: 'On disk' },
                { value: 'not_owned', label: 'Not on disk' },
              ]}
            />
            <Pick
              label="Monitored"
              value={filters.monitored}
              onChange={(monitored) => set({ monitored: monitored as Filters['monitored'] })}
              options={[
                { value: '1', label: 'Monitored' },
                { value: '0', label: 'Not monitored' },
              ]}
            />
            <Pick
              label="Padlock"
              value={filters.locked}
              onChange={(locked) => set({ locked: locked as Filters['locked'] })}
              options={[
                { value: '1', label: 'Locked' },
                { value: '0', label: 'Not locked' },
              ]}
            />
            {facets.quality_profiles.length ? (
              <Pick
                label="Quality profile"
                value={filters.quality_profile}
                onChange={(quality_profile) => set({ quality_profile })}
                options={[
                  { value: 'default', label: 'Default' },
                  ...facets.quality_profiles.map((p) => ({ value: String(p.id), label: p.name })),
                ]}
              />
            ) : null}
            <label className={s.field}>
              <span className={s.fieldLabel}>Year</span>
              <span className={s.years}>
                <input
                  className={s.input}
                  inputMode="numeric"
                  placeholder="From"
                  aria-label="Year from"
                  value={filters.year_from}
                  onChange={(e) =>
                    set({ year_from: e.target.value.replace(/\D/g, '').slice(0, 4) })
                  }
                />
                <input
                  className={s.input}
                  inputMode="numeric"
                  placeholder="To"
                  aria-label="Year to"
                  value={filters.year_to}
                  onChange={(e) => set({ year_to: e.target.value.replace(/\D/g, '').slice(0, 4) })}
                />
              </span>
            </label>
          </>
        ) : null}
      </div>
      {titlesOnly ? (
        <div className={s.panelMore}>
          <span className={s.fieldLabel}>Also show only</span>
          {(['unmonitored', 'unwatched', 'not_owned'] as const).map((need) => (
            <button
              key={need}
              type="button"
              className={s.need}
              aria-pressed={filters.needs.includes(need)}
              title={NEED_COPY[need][side]}
              onClick={() =>
                set({
                  needs: filters.needs.includes(need)
                    ? filters.needs.filter((n) => n !== need)
                    : [...filters.needs, need],
                })
              }
            >
              {NEED_COPY[need].label}
              <span className={s.needCount}>{(facets.needs[need] ?? 0).toLocaleString()}</span>
            </button>
          ))}
        </div>
      ) : null}
      <div className={s.panelFoot}>
        <button
          type="button"
          className={s.linkBtn}
          onClick={() => onChange({ ...EMPTY_FILTERS, search: filters.search })}
        >
          Clear all
        </button>
        <button type="button" className="pgf-btn" onClick={onClose}>
          Done
        </button>
      </div>
    </section>
  );
}

/** the filters that are on, each with a way to turn it off */
export function ActiveFilters({
  side,
  filters,
  facets,
  onChange,
}: {
  side: Side;
  filters: Filters;
  facets: Facets | null;
  onChange: (next: Filters) => void;
}) {
  const chips: { key: string; label: string; clear: Partial<Filters> }[] = [];
  const add = (key: keyof Filters, label: string) =>
    chips.push({ key, label, clear: { [key]: '' } as Partial<Filters> });
  if (filters.resolution) add('resolution', filters.resolution);
  if (filters.codec) add('codec', codecName(filters.codec));
  if (filters.hdr) add('hdr', filters.hdr === '1' ? 'HDR' : 'SDR only');
  if (filters.audio_language)
    add('audio_language', `Audio: ${languageName(filters.audio_language)}`);
  if (filters.subtitle_language)
    add('subtitle_language', `Subtitles: ${languageName(filters.subtitle_language)}`);
  if (filters.drive) add('drive', filters.drive);
  if (filters.genre) add('genre', filters.genre);
  if (filters.status) add('status', filters.status === 'owned' ? 'On disk' : 'Not on disk');
  if (filters.monitored)
    add('monitored', filters.monitored === '1' ? 'Monitored' : 'Not monitored');
  if (filters.locked) add('locked', filters.locked === '1' ? 'Locked' : 'Not locked');
  if (filters.video_library)
    add(
      'video_library',
      facets?.libraries.find((l) => String(l.id) === filters.video_library)?.name ?? 'Library',
    );
  if (filters.quality_profile)
    add(
      'quality_profile',
      filters.quality_profile === 'default'
        ? 'Default profile'
        : (facets?.quality_profiles.find((p) => String(p.id) === filters.quality_profile)?.name ??
            'Profile'),
    );
  if (filters.year_from || filters.year_to)
    chips.push({
      key: 'year',
      label: `${filters.year_from || '…'} to ${filters.year_to || '…'}`,
      clear: { year_from: '', year_to: '' },
    });
  for (const need of filters.needs.filter((n) =>
    ['unmonitored', 'unwatched', 'not_owned'].includes(n),
  ))
    chips.push({
      key: need,
      label: NEED_COPY[need].label,
      clear: { needs: filters.needs.filter((n) => n !== need) },
    });
  if (!chips.length) return null;
  return (
    <div className={s.chips} aria-label={`Filters on ${side}`}>
      {chips.map((chip) => (
        <button
          key={chip.key}
          type="button"
          className={s.chip}
          title="Remove this filter"
          onClick={() => onChange({ ...filters, ...chip.clear })}
        >
          {chip.label}
          <svg viewBox="0 0 12 12" aria-hidden="true">
            <path
              d="M3 3l6 6M9 3l-6 6"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              strokeLinecap="round"
            />
          </svg>
        </button>
      ))}
    </div>
  );
}
