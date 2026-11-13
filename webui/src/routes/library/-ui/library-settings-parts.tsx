/**
 * The shared pieces of View Settings and Artist Settings: the pills that
 * split a sheet by what a setting changes, a switch row, a chip you press,
 * and the small drawings a choice is picked by.
 *
 * Every control here is the input it always was. A switch and a chip are
 * checkboxes, a choice is a radio: only how they are drawn changed.
 */

import type { ReactNode } from 'react';

import ls from './library-settings.module.css';

export { ls };

export interface SettingsPill<K extends string> {
  key: K;
  label: string;
  /** who the pane's settings belong to ("all profiles") */
  note?: string;
}

export function SettingsPills<K extends string>({
  label,
  pills,
  value,
  onChange,
}: {
  label: string;
  pills: readonly SettingsPill<K>[];
  value: K;
  onChange: (key: K) => void;
}) {
  return (
    <div className={ls.pills} role="tablist" aria-label={label}>
      {pills.map((pill) => (
        <button
          key={pill.key}
          type="button"
          role="tab"
          aria-selected={pill.key === value}
          className={`${ls.pill} ${pill.key === value ? ls.pillActive : ''}`}
          onClick={() => onChange(pill.key)}
        >
          {pill.label}
          {pill.note ? <small>{pill.note}</small> : null}
        </button>
      ))}
    </div>
  );
}

/** A titled set of options, with the line that says what they change. */
export function SettingsGroup({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={ls.group}>
      <h4 className={ls.groupTitle}>{title}</h4>
      {hint ? <p className={ls.groupHint}>{hint}</p> : null}
      {children}
    </section>
  );
}

/** One option with a switch: a labelled checkbox, drawn as a row. */
export function SwitchRow({
  label,
  sub,
  note,
  checked,
  disabled,
  title,
  onChange,
}: {
  label: string;
  /** a second line under the label */
  sub?: ReactNode;
  /** an example on the right ("16bit/44.1kHz") */
  note?: ReactNode;
  checked: boolean;
  disabled?: boolean;
  title?: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className={ls.row}>
      <label className={ls.rowLabel} title={title}>
        <span className={ls.rowText}>
          <strong>{label}</strong>
          {sub ? <small>{sub}</small> : null}
        </span>
        {note ? <span className={ls.rowNote}>{note}</span> : null}
        <input
          type="checkbox"
          className={ls.switch}
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
      </label>
    </div>
  );
}

/** A checkbox drawn as a chip to press. `dot` is a provider's own color. */
export function ChipCheck({
  label,
  note,
  dot,
  checked,
  disabled,
  title,
  onChange,
}: {
  label: string;
  note?: string;
  dot?: string;
  checked: boolean;
  disabled?: boolean;
  title?: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={ls.chip} title={title}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      {dot !== undefined ? (
        <span className={ls.chipDot} style={dot ? { background: dot } : undefined} aria-hidden />
      ) : null}
      {label}
      {note ? <small>{note}</small> : null}
    </label>
  );
}

export type ChoicePicture = 'rich' | 'compact' | 'title' | 'pencil';

const PICTURES: Record<ChoicePicture, { className: string; body: ReactNode }> = {
  rich: {
    className: ls.picRich,
    body: (
      <>
        <i />
        <span>
          <i />
          <i />
          <i />
        </span>
      </>
    ),
  },
  compact: {
    className: ls.picCompact,
    body: (
      <>
        <i />
        <span>
          <i />
          <i />
          <i />
          <i />
        </span>
      </>
    ),
  },
  title: {
    className: ls.picTitle,
    body: (
      <>
        <i />
        <i />
      </>
    ),
  },
  pencil: {
    className: ls.picPencil,
    body: (
      <>
        <i />
        <i />
        <i />
      </>
    ),
  },
};

/** One of a set of exclusive choices: a radio, drawn as a card with a small
 *  picture of what it gives you. */
export function ChoiceCard({
  name,
  picture,
  title,
  text,
  checked,
  onChange,
}: {
  /** the radio group's name */
  name: string;
  picture: ChoicePicture;
  title: string;
  text: string;
  checked: boolean;
  onChange: () => void;
}) {
  const drawn = PICTURES[picture];
  return (
    <label className={`${ls.choice} ${checked ? ls.choiceActive : ''}`}>
      <input type="radio" name={name} className={ls.srOnly} checked={checked} onChange={onChange} />
      <span className={`${ls.pic} ${drawn.className}`} aria-hidden>
        {drawn.body}
      </span>
      <span className={ls.choiceTitle}>{title}</span>
      <span className={ls.choiceText}>{text}</span>
    </label>
  );
}

export interface PreviewProvider {
  key: string;
  abbr: string;
  color?: string;
}

/** What one row of the track table reads like for a given column. A sample,
 *  not library data: it is here to show the layout the settings produce. */
const SAMPLE_CELL: Record<string, string> = {
  title: 'Track title',
  disc: '1',
  artists: 'Artist',
  duration: '3:42',
  bpm: '120',
  media_server: 'Plex',
  profile: 'Default',
  features: 'Lyrics',
  metadata: 'Complete',
  acoustid: 'Verified',
  file_size: '24.6 MB',
  file_path: '…/Artist/Album/01 - Track title.flac',
  play: '▶',
};

/**
 * The track table as the settings above it make it: the visible columns in
 * their order, the Quality badge with the parts it shows, and the Match
 * column's provider chips. It redraws as a setting changes, so a pick is seen
 * before the dialog is closed.
 */
export function TrackTablePreview({
  columns,
  labels,
  quality,
  providers,
}: {
  /** the visible column keys, left to right */
  columns: readonly string[];
  labels: Readonly<Record<string, string>>;
  /** the parts of the Quality badge that are on */
  quality: readonly string[];
  providers: readonly PreviewProvider[];
}) {
  return (
    <div className={ls.strip} aria-label="How the track table looks with these settings">
      <table className={ls.stripTable}>
        <thead>
          <tr>
            {columns.map((key) => (
              <th key={key}>{key === 'play' ? '' : labels[key]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            {columns.map((key) => {
              if (key === 'quality') {
                return (
                  <td key={key}>
                    {quality.length > 0 ? (
                      <span className={ls.stripBadge}>
                        {quality.map((part) => (
                          <span key={part}>{part}</span>
                        ))}
                      </span>
                    ) : null}
                  </td>
                );
              }
              if (key === 'match') {
                return (
                  <td key={key}>
                    {providers.map((provider) => (
                      <span
                        key={provider.key}
                        className={ls.stripProvider}
                        style={provider.color ? { color: provider.color } : undefined}
                      >
                        {provider.abbr}
                      </span>
                    ))}
                  </td>
                );
              }
              return (
                <td key={key} className={key === 'title' ? ls.stripTitle : undefined}>
                  {SAMPLE_CELL[key] ?? ''}
                </td>
              );
            })}
          </tr>
        </tbody>
      </table>
    </div>
  );
}
