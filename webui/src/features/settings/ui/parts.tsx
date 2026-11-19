/**
 * The Settings page's parts (settings-bic DESIGN.md section 4): one row, one
 * set of controls, one card, one hero. Every tab is built from these, so a
 * switch is the same switch everywhere and a label never ends in a colon.
 *
 * They draw and report; they hold no setting. A control keeps the `id` its
 * setting always had, because the search, the help palette and the key guard
 * (guard/) find it by that.
 */

import clsx from 'clsx';
import {
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
  useId,
  useRef,
  useState,
} from 'react';

import { REDACTED_SECRET_SENTINEL, helpFirstLine } from '../tabs/parts';
import { Icon, type IconName } from './icons';
import s from './settings.module.css';

export type Tone = 'idle' | 'ok' | 'warn' | 'bad' | 'busy';

// ── buttons ──

export function Btn({
  kind,
  small,
  icon,
  iconOnly,
  busy,
  className,
  children,
  ...rest
}: {
  kind?: 'primary' | 'ghost' | 'danger';
  small?: boolean;
  icon?: IconName;
  /** a square button: pass `aria-label` */
  iconOnly?: boolean;
  /** working: the icon spins and a second press does nothing */
  busy?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...rest}
      className={clsx(
        s.btn,
        kind === 'primary' && s.btnPrimary,
        kind === 'ghost' && s.btnGhost,
        kind === 'danger' && s.btnDanger,
        small && s.btnSm,
        iconOnly && s.btnIcon,
        busy && s.btnBusy,
        className,
      )}
      aria-busy={busy || undefined}
      disabled={rest.disabled || busy}
    >
      {icon ? (
        <span className={busy ? s.spin : undefined} style={{ display: 'inline-flex' }}>
          <Icon name={busy ? 'refresh' : icon} size={iconOnly ? 16 : 15} />
        </span>
      ) : null}
      {children}
    </button>
  );
}

// ── pills ──

export interface PillItem {
  key: string;
  label: string;
  /** something on it needs a look */
  dot?: boolean;
  count?: number;
}

/** the views of a tab. A tablist: arrows move, the panel is the caller's */
export function Pills({
  label,
  items,
  value,
  onChange,
  idPrefix,
}: {
  label: string;
  items: PillItem[];
  value: string;
  onChange: (key: string) => void;
  /** tab ids are `${idPrefix}-${key}`, for the panels' aria-labelledby */
  idPrefix?: string;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const at = items.findIndex((item) => item.key === value);
    const next = items[(at + step + items.length) % items.length];
    onChange(next.key);
    const buttons = event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]');
    buttons[items.indexOf(next)]?.focus();
  };
  return (
    <div className={s.pills} role="tablist" aria-label={label} data-ui="" onKeyDown={onKeyDown}>
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          id={idPrefix ? `${idPrefix}-${item.key}` : undefined}
          aria-selected={item.key === value}
          tabIndex={item.key === value ? 0 : -1}
          data-view={item.key}
          className={clsx(s.pill, item.key === value && s.pillOn)}
          onClick={() => onChange(item.key)}
        >
          {item.label}
          {item.count !== undefined ? <span className={s.pillCount}>{item.count}</span> : null}
          {item.dot ? <span className={s.pillDot} title="Needs a look" /> : null}
        </button>
      ))}
    </div>
  );
}

// ── hero ──

export interface HeroStat {
  value: ReactNode;
  label: string;
}

/** the tab's settings read back as one sentence, and up to three figures */
export function Hero({ children, stats }: { children: ReactNode; stats?: HeroStat[] }) {
  return (
    <div className={s.hero}>
      <p className={s.heroLine}>{children}</p>
      {stats?.length ? (
        <div className={s.stats}>
          {stats.map((stat) => (
            <div className={s.stat} key={stat.label}>
              <b className={s.statValue}>{stat.value}</b>
              <span className={s.statLabel}>{stat.label}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** the words of a hero sentence that need a look */
export function HeroWarn({ children }: { children: ReactNode }) {
  return <span className={s.heroWarn}>{children}</span>;
}

// ── section label, card ──

export function SectionLabel({
  title,
  hint,
  action,
  id,
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  id?: string;
}) {
  return (
    <div className={s.eyebrow}>
      <h3 className={s.eyebrowLabel} id={id}>
        {title}
      </h3>
      {hint ? <span className={s.eyebrowHint}>{hint}</span> : null}
      {action ? <span className={s.eyebrowAction}>{action}</span> : null}
    </div>
  );
}

export function Card({
  title,
  hint,
  actions,
  danger,
  children,
  className,
  ...rest
}: {
  title?: string;
  hint?: ReactNode;
  actions?: ReactNode;
  /** a danger zone: what is in it deletes or cannot be undone */
  danger?: boolean;
  children: ReactNode;
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'title'>) {
  return (
    <div {...rest} data-card={title} className={clsx(s.card, danger && s.danger, className)}>
      {title ? (
        <div className={s.cardHead}>
          <div>
            <h4 className={s.cardTitle}>{title}</h4>
            {hint ? <p className={s.cardHint}>{hint}</p> : null}
          </div>
          {actions ? <div className={s.cardActions}>{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </div>
  );
}

// ── rows ──

export interface RowProps {
  label: ReactNode;
  /** the control's id: the label points at it */
  htmlFor?: string;
  /** one line under the label. A long string shows its first sentence and
   *  keeps the rest behind the "i" */
  help?: ReactNode;
  /** more to read, behind the "i" (when `help` is not a long string) */
  more?: ReactNode;
  /** depends on the row above: indented */
  sub?: boolean;
  /** cannot be used right now: dimmed */
  off?: boolean;
  /** the control sits under the label, full width (templates, lists) */
  stack?: boolean;
  /** a field, not a switch: goes under its label on a phone */
  field?: boolean;
  /** what is wrong with it; the row is marked */
  error?: string | null;
  /** changed and not saved yet */
  changed?: boolean;
  /** saved just now through its own endpoint */
  saved?: boolean;
  id?: string;
  hidden?: boolean;
  /** a brand mark before the label, beside both lines */
  mark?: ReactNode;
  children?: ReactNode;
}

export function Row({
  label,
  htmlFor,
  help,
  more,
  sub,
  off,
  stack,
  field,
  error,
  changed,
  saved,
  id,
  hidden,
  mark,
  children,
}: RowProps) {
  const [open, setOpen] = useState(false);
  const moreId = useId();
  let line: ReactNode = help;
  let rest: ReactNode = more;
  if (typeof help === 'string' && !more) {
    const split = helpFirstLine(help);
    line = split.line;
    rest = split.rest ? help : null;
  }
  return (
    <div
      id={id}
      hidden={hidden}
      data-row=""
      className={clsx(
        s.row,
        sub && s.rowSub,
        off && s.rowOff,
        stack && s.rowStack,
        (field || stack) && s.rowField,
        error && s.rowError,
      )}
    >
      <div className={mark ? s.rowMark : undefined}>
        {mark}
        <div>
          <div className={s.rowLabel}>
            {changed ? <span className={s.changed} title="Not saved yet" /> : null}
            {htmlFor ? <label htmlFor={htmlFor}>{label}</label> : <span>{label}</span>}
            {rest ? (
              <button
                type="button"
                className={s.helpBtn}
                aria-expanded={open}
                aria-controls={moreId}
                aria-label="More about this setting"
                onClick={() => setOpen((o) => !o)}
              >
                i
              </button>
            ) : null}
            {saved ? (
              <span className={s.rowSaved} role="status">
                Saved
              </span>
            ) : null}
          </div>
          {line ? <div className={s.rowHelp}>{line}</div> : null}
          {rest ? (
            <div className={s.rowMore} id={moreId} hidden={!open}>
              {rest}
            </div>
          ) : null}
          {error ? (
            <div className={s.rowErrorText} role="alert">
              {error}
            </div>
          ) : null}
        </div>
      </div>
      {children ? <div className={s.rowCtl}>{children}</div> : null}
    </div>
  );
}

/** the rare settings at the foot of a card: closed until asked for, and it
 *  says how many and which. What is inside stays mounted while closed */
export function More({
  label = 'More options',
  names,
  count,
  defaultOpen = false,
  children,
}: {
  label?: string;
  /** what is inside, in a few words */
  names?: string;
  count?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();
  return (
    <>
      <button
        type="button"
        className={s.more}
        aria-expanded={open}
        aria-controls={bodyId}
        data-more=""
        onClick={() => setOpen((o) => !o)}
      >
        <span className={s.moreChev}>
          <Icon name="chev" size={14} />
        </span>
        {label}
        {count !== undefined || names ? (
          <span className={s.moreCount}>
            {[count !== undefined ? `· ${count}` : '', names ? `· ${names}` : '']
              .filter(Boolean)
              .join(' ')}
          </span>
        ) : null}
      </button>
      <div className={s.moreBody} id={bodyId} hidden={!open} data-more-body="">
        {children}
      </div>
    </>
  );
}

// ── controls ──

/** on or off. A real checkbox, so its id, `checked` and change event are what
 *  they were when it was a tick box */
export function Switch({
  className,
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>) {
  return <input {...rest} type="checkbox" role="switch" className={clsx(s.switch, className)} />;
}

export interface Option {
  value: string;
  label: string;
  disabled?: boolean;
}

/** a choice of two to four short options */
export function Segmented({
  id,
  label,
  value,
  options,
  onChange,
  disabled,
  panel,
  itemAttr,
}: {
  id?: string;
  /** read to a screen reader; the row's label when it has one */
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  disabled?: boolean;
  /** a data attribute each option carries, set to its value (a hook other
   *  code finds the option by: `data-vq-codec`) */
  itemAttr?: `data-${string}`;
  /** it switches what a panel shows: a tablist, each tab controlling the
   *  panel this names (one id, or one per value) */
  panel?: string | ((value: string) => string);
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0;
    if (!step) return;
    event.preventDefault();
    const usable = options.filter((o) => !o.disabled);
    const at = usable.findIndex((o) => o.value === value);
    const next = usable[(at + step + usable.length) % usable.length];
    if (!next) return;
    onChange(next.value);
    event.currentTarget
      .querySelector<HTMLElement>(`[data-value="${CSS.escape(next.value)}"]`)
      ?.focus();
  };
  return (
    <div
      id={id}
      className={s.seg}
      role={panel ? 'tablist' : 'radiogroup'}
      aria-label={label}
      data-value={value}
      onKeyDown={onKeyDown}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role={panel ? 'tab' : 'radio'}
          aria-checked={panel ? undefined : option.value === value}
          aria-selected={panel ? option.value === value : undefined}
          aria-controls={typeof panel === 'function' ? panel(option.value) : panel}
          tabIndex={option.value === value ? 0 : -1}
          data-value={option.value}
          {...(itemAttr ? { [itemAttr]: option.value } : {})}
          disabled={disabled || option.disabled}
          className={clsx(s.segItem, option.value === value && s.segOn)}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** the browser's own select underneath, so a phone gets its own picker */
export function Select({
  className,
  children,
  options,
  ...rest
}: { options?: Option[]; ref?: Ref<HTMLSelectElement> } & SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={clsx(s.field, s.fieldSelect, rest.disabled && s.fieldDisabled, className)}>
      <select {...rest} className={s.select}>
        {options
          ? options.map((o) => (
              <option key={o.value} value={o.value} disabled={o.disabled}>
                {o.label}
              </option>
            ))
          : children}
      </select>
      <span className={s.selectChev}>
        <Icon name="down" size={14} />
      </span>
    </span>
  );
}

interface FieldLook {
  /** 420px */
  wide?: boolean;
  /** the whole row */
  full?: boolean;
  mono?: boolean;
  /** red border; the reason goes in the row's `error` */
  invalid?: boolean;
  /** at the right edge, inside the field: seconds, GB, kbps */
  unit?: string;
  /** before the text */
  icon?: IconName;
  /** after the text (a lock, an eye) */
  trailing?: ReactNode;
  /** the input itself (a field that saves on the browser's change event) */
  ref?: Ref<HTMLInputElement>;
}

export function TextField({
  wide,
  full,
  mono,
  invalid,
  unit,
  icon,
  trailing,
  className,
  ...rest
}: FieldLook & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <span
      className={clsx(
        s.field,
        wide && s.fieldWide,
        full && s.fieldFull,
        rest.type === 'number' && !wide && !full && s.fieldNum,
        invalid && s.fieldError,
        rest.disabled && s.fieldDisabled,
        className,
      )}
    >
      {icon ? (
        <span className={s.fieldIcon}>
          <Icon name={icon} size={15} />
        </span>
      ) : null}
      <input
        type="text"
        {...rest}
        aria-invalid={invalid || undefined}
        className={clsx(s.input, mono && s.inputMono)}
      />
      {unit ? <span className={s.unit}>{unit}</span> : null}
      {trailing}
    </span>
  );
}

/** a number with its unit inside the field */
export function NumberField(
  props: FieldLook & Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>,
) {
  return <TextField {...props} type="number" />;
}

export function TextArea({
  invalid,
  mono,
  className,
  ...rest
}: { invalid?: boolean; mono?: boolean } & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <span className={clsx(s.field, s.fieldFull, s.fieldArea, invalid && s.fieldError, className)}>
      <textarea {...rest} className={clsx(s.input, s.textarea, mono && s.inputMono)} />
    </span>
  );
}

/**
 * A saved secret. It arrives as a mask (ConfigManager.REDACTED_SENTINEL),
 * never the secret: focusing the mask empties the field so a new one is typed,
 * leaving it untouched puts the mask back and saves nothing, and real typing
 * is a real change (an emptied field too). The eye shows what was just typed;
 * a saved secret cannot be shown, the browser never had it.
 */
export function SecretField({
  value,
  onChange,
  wide,
  full = true,
  invalid,
  className,
  ...rest
}: {
  value: string;
  onChange: (value: string) => void;
  wide?: boolean;
  full?: boolean;
  invalid?: boolean;
} & Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'value' | 'onChange' | 'type' | 'onFocus' | 'onBlur'
>) {
  const [cleared, setCleared] = useState(false);
  const [shown, setShown] = useState(false);
  const masked = value === REDACTED_SECRET_SENTINEL;
  const typed = !masked && value !== '';
  return (
    <span
      className={clsx(
        s.field,
        wide && s.fieldWide,
        full && !wide && s.fieldFull,
        invalid && s.fieldError,
        rest.disabled && s.fieldDisabled,
        className,
      )}
    >
      <input
        {...rest}
        type={shown && typed ? 'text' : 'password'}
        className={s.input}
        value={cleared ? '' : value}
        data-saved={masked ? '' : undefined}
        onFocus={() => {
          if (masked) setCleared(true);
        }}
        onBlur={() => setCleared(false)}
        onChange={(event) => {
          setCleared(false);
          onChange(event.target.value);
        }}
      />
      {typed ? (
        <button
          type="button"
          className={s.fieldBtn}
          aria-label={shown ? 'Hide what you typed' : 'Show what you typed'}
          aria-pressed={shown}
          onClick={() => setShown((v) => !v)}
        >
          <Icon name={shown ? 'eyeOff' : 'eye'} size={15} />
        </button>
      ) : null}
    </span>
  );
}

export interface FolderCheck {
  tone: Tone;
  text: string;
}

/**
 * A folder. Mono text, a lock that keeps a working path from being changed by
 * a stray key, and the folder check under it in words. `inputProps` carries
 * the `data-folder-*` attributes static/folder-check.js reads.
 */
export function PathField({
  id,
  value,
  onChange,
  locked,
  onLock,
  check,
  placeholder,
  invalid,
  inputProps,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  /** undefined: this path has no lock */
  locked?: boolean;
  onLock?: (locked: boolean) => void;
  check?: FolderCheck | null;
  placeholder?: string;
  invalid?: boolean;
  inputProps?: InputHTMLAttributes<HTMLInputElement> & Record<`data-${string}`, string>;
}) {
  return (
    <div className={s.fieldStack}>
      <TextField
        {...inputProps}
        id={id}
        full
        mono
        icon="folder"
        invalid={invalid}
        value={value}
        placeholder={placeholder}
        readOnly={locked === true}
        onChange={(event) => onChange(event.target.value)}
        trailing={
          locked !== undefined && onLock ? (
            <button type="button" className={s.fieldBtn} onClick={() => onLock(!locked)}>
              {locked ? 'Unlock' : 'Lock'}
            </button>
          ) : null
        }
      />
      {check ? (
        <div className={s.check} data-tone={check.tone} role="status">
          <Icon
            name={check.tone === 'ok' ? 'check' : check.tone === 'idle' ? 'info' : 'warn'}
            size={13}
          />
          {check.text}
        </div>
      ) : null}
    </div>
  );
}

export interface TemplatePreview {
  folders: string[];
  file?: string;
}

/** a naming template and what it produces: "Saves as" folders, then the file */
export function TemplateField({
  id,
  value,
  onChange,
  preview,
  previewNote,
  invalid,
  placeholder,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  preview?: TemplatePreview | null;
  /** shown where the preview would be when there is none */
  previewNote?: string;
  invalid?: boolean;
  placeholder?: string;
}) {
  return (
    <div className={s.fieldStack}>
      <TextField
        id={id}
        full
        mono
        invalid={invalid}
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
      />
      {preview ? (
        <div className={s.preview} data-template-preview="">
          Saves as
          {preview.folders.map((folder, index) => (
            // a template can name the same folder twice
            // eslint-disable-next-line react/no-array-index-key
            <span className={s.crumb} key={`${index}-${folder}`}>
              {folder}
            </span>
          ))}
          {preview.file ? <span className={clsx(s.crumb, s.crumbFile)}>{preview.file}</span> : null}
        </div>
      ) : previewNote ? (
        <div className={s.preview}>{previewNote}</div>
      ) : null}
    </div>
  );
}

/** small sets that are on or off together (tags to embed, formats allowed) */
export function Chips({
  label,
  options,
  value,
  onToggle,
  disabled,
  id,
  hidden,
  itemAttr,
}: {
  label: string;
  options: Array<{ key: string; label: string; id?: string }>;
  value: string[];
  onToggle: (key: string, on: boolean) => void;
  disabled?: boolean;
  id?: string;
  hidden?: boolean;
  /** a data attribute each chip carries, set to its key (`data-vq-reject`) */
  itemAttr?: `data-${string}`;
}) {
  return (
    <div className={s.chips} role="group" aria-label={label} id={id} hidden={hidden}>
      {options.map((option) => {
        const on = value.includes(option.key);
        return (
          <button
            key={option.key}
            type="button"
            id={option.id}
            role="checkbox"
            aria-checked={on}
            data-key={option.key}
            {...(itemAttr ? { [itemAttr]: option.key } : {})}
            disabled={disabled}
            className={clsx(s.chip, on && s.chipOn)}
            onClick={() => onToggle(option.key, !on)}
          >
            {on ? (
              <span className={s.chipTick}>
                <Icon name="check" size={12} />
              </span>
            ) : null}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** a list of words that can each be taken off (genres) */
/**
 * Chips that are real tick boxes underneath: for a set of switches that save
 * as a form control does (its change event, `checked`), drawn as chips.
 */
export function CheckChips({
  id,
  label,
  options,
  value,
  onToggle,
}: {
  id?: string;
  label: string;
  options: Array<{ value: string; label: string }>;
  value: string[];
  onToggle: (value: string, on: boolean) => void;
}) {
  return (
    <div className={s.chips} role="group" aria-label={label} id={id}>
      {options.map((option) => {
        const on = value.includes(option.value);
        return (
          <label key={option.value} className={clsx(s.chip, s.chipCheck, on && s.chipOn)}>
            <input
              type="checkbox"
              className={s.chipInput}
              value={option.value}
              checked={on}
              onChange={(event) => onToggle(option.value, event.target.checked)}
            />
            {on ? (
              <span className={s.chipTick}>
                <Icon name="check" size={12} />
              </span>
            ) : null}
            {option.label}
          </label>
        );
      })}
    </div>
  );
}

export function ChipList({
  id,
  items,
  onRemove,
  removeLabel = (item) => `Remove ${item}`,
  empty,
  itemAttr = 'data-item',
}: {
  id?: string;
  /** the attribute each remove button names its item in */
  itemAttr?: `data-${string}`;
  items: string[];
  onRemove: (item: string) => void;
  removeLabel?: (item: string) => string;
  empty?: string;
}) {
  if (!items.length && empty) {
    return (
      <p className={s.listEmpty} id={id}>
        {empty}
      </p>
    );
  }
  return (
    <div className={s.chips} id={id}>
      {items.map((item) => (
        <span className={s.chip} key={item}>
          {item}
          <button
            type="button"
            className={s.chipX}
            {...{ [itemAttr]: item }}
            aria-label={removeLabel(item)}
            onClick={() => onRemove(item)}
          >
            <Icon name="x" size={12} />
          </button>
        </span>
      ))}
    </div>
  );
}

/** rows of one field and a remove, with "+ Add" under them */
export function ListEditor({
  id,
  items,
  onChange,
  placeholder,
  addLabel = 'Add',
  empty,
  mono,
  itemLabel = (index) => `Item ${index + 1}`,
  tagFor,
}: {
  id?: string;
  items: string[];
  onChange: (items: string[]) => void;
  placeholder?: string;
  addLabel?: string;
  /** said when the list is empty */
  empty?: string;
  mono?: boolean;
  itemLabel?: (index: number) => string;
  /** a word beside a row ("default") */
  tagFor?: (item: string, index: number) => string | null;
}) {
  const list = useRef<HTMLDivElement>(null);
  const add = () => {
    onChange([...items, '']);
    // the new row's field, once it is drawn
    requestAnimationFrame(() => {
      const inputs = list.current?.querySelectorAll<HTMLInputElement>('input');
      inputs?.[inputs.length - 1]?.focus();
    });
  };
  return (
    <div className={s.list} id={id} ref={list}>
      {items.length === 0 && empty ? <p className={s.listEmpty}>{empty}</p> : null}
      {items.map((item, index) => {
        const tag = tagFor?.(item, index);
        return (
          // rows have no identity but their place
          // eslint-disable-next-line react/no-array-index-key
          <div className={s.listRow} key={index}>
            <TextField
              full
              mono={mono}
              value={item}
              placeholder={placeholder}
              aria-label={itemLabel(index)}
              onChange={(event) =>
                onChange(items.map((old, i) => (i === index ? event.target.value : old)))
              }
            />
            {tag ? <span className={s.tag}>{tag}</span> : null}
            <Btn
              kind="ghost"
              small
              iconOnly
              icon="x"
              aria-label={`Remove ${item || itemLabel(index)}`}
              onClick={() => onChange(items.filter((_, i) => i !== index))}
            />
          </div>
        );
      })}
      <div>
        <Btn small icon="plus" onClick={add}>
          {addLabel}
        </Btn>
      </div>
    </div>
  );
}

export function Tag({
  accent,
  danger,
  children,
}: {
  accent?: boolean;
  /** it says what deletes */
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <span className={clsx(s.tag, accent && s.tagAccent, danger && s.tagDanger)}>{children}</span>
  );
}

// ── status, marks, notes ──

/** a dot and words. Never a dot alone */
export function StatusWords({
  tone = 'idle',
  children,
  className,
  id,
  live,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  id?: string;
  /** announce a change (a test's answer) */
  live?: boolean;
}) {
  return (
    <span
      id={id}
      className={clsx(s.st, className)}
      data-tone={tone}
      role={live ? 'status' : undefined}
      aria-live={live ? 'polite' : undefined}
      title={typeof children === 'string' ? children : undefined}
    >
      <span className={s.stText}>{children}</span>
    </span>
  );
}

/** a brand mark in a rounded square: its logo, or one line icon on the
 *  brand's own colour when there is no logo file */
export function Mark({
  src,
  icon = 'link',
  brand,
  size,
  pad,
  invert,
}: {
  src?: string | null;
  icon?: IconName;
  /** "r, g, b" */
  brand?: string;
  size?: 'sm' | 'lg';
  /** the logo is a glyph, not a full tile: drawn smaller, centred */
  pad?: boolean;
  /** a dark mark, drawn white */
  invert?: boolean;
}) {
  const [broken, setBroken] = useState(false);
  const image = src && !broken;
  return (
    <span
      className={clsx(
        s.mark,
        size === 'sm' && s.markSm,
        size === 'lg' && s.markLg,
        image && pad && s.markPad,
        image && invert && s.markInv,
        !image && s.markBrand,
      )}
      style={!image && brand ? ({ '--brand': brand } as React.CSSProperties) : undefined}
      aria-hidden="true"
    >
      {image ? (
        <img src={src} alt="" onError={() => setBroken(true)} />
      ) : (
        <Icon name={icon} size={size === 'sm' ? 16 : 18} />
      )}
    </span>
  );
}

export function Note({
  info,
  children,
  action,
}: {
  /** something to know, not something wrong */
  info?: boolean;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={clsx(s.note, info && s.noteInfo)} role={info ? undefined : 'note'}>
      <span className={s.noteIcon}>
        <Icon name={info ? 'info' : 'warn'} />
      </span>
      <div className={s.noteBody}>{children}</div>
      {action}
    </div>
  );
}

// ── action row, tiles, danger ──

/** one Test and its answer beside it, in words */
export function TestAction({
  label = 'Test connection',
  buttonId,
  statusId,
  tone,
  said,
  busy,
  onTest,
}: {
  label?: string;
  buttonId?: string;
  statusId?: string;
  tone: Tone;
  /** what the server said; nothing until a test has run */
  said: string;
  busy?: boolean;
  onTest: () => void;
}) {
  return (
    <>
      <Btn id={buttonId} icon="refresh" busy={busy} onClick={onTest}>
        {busy ? 'Testing…' : label}
      </Btn>
      <StatusWords id={statusId} tone={busy ? 'busy' : tone} live>
        {busy ? 'Testing…' : said}
      </StatusWords>
    </>
  );
}

/** an action as a row: what it does on the left, its button and answer on the right */
export function ActionRow({
  label,
  help,
  children,
  ...row
}: { label: ReactNode; help?: ReactNode; children: ReactNode } & Pick<
  RowProps,
  'id' | 'sub' | 'off' | 'hidden'
>) {
  return (
    <Row label={label} help={help} {...row}>
      {children}
    </Row>
  );
}

export function Tiles({ children }: { children: ReactNode }) {
  return <div className={s.tiles}>{children}</div>;
}

/** a thing with a status that opens a sheet */
export function Tile({
  mark,
  name,
  tone,
  status,
  on,
  empty,
  ...rest
}: {
  mark: ReactNode;
  name: string;
  tone?: Tone;
  status: string;
  /** the one in use (the primary source, the chosen server) */
  on?: boolean;
  /** not set up */
  empty?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...rest}
      className={clsx(s.tile, on && s.tileOn, empty && s.tileEmpty, rest.className)}
    >
      {mark}
      <span>
        <span className={s.tileName}>{name}</span>
        <StatusWords tone={tone}>{status}</StatusWords>
      </span>
      <span className={s.tileChev}>
        <Icon name="chev" size={14} />
      </span>
    </button>
  );
}

/** a row whose button deletes or cannot be undone. The caller confirms with
 *  SoulSync's own dialog before it acts */
export function DangerRow({
  label,
  help,
  action,
  onAction,
  busy,
  id,
}: {
  label: ReactNode;
  help?: ReactNode;
  action: string;
  onAction: () => void;
  busy?: boolean;
  id?: string;
}) {
  return (
    <Row label={label} help={help}>
      <Btn kind="danger" id={id} busy={busy} onClick={onAction}>
        {action}
      </Btn>
    </Row>
  );
}

// ── a list of things in a sheet (HiFi instances, indexers) ──

export function Items({
  id,
  children,
  className,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & { ref?: Ref<HTMLDivElement> }) {
  return (
    <div {...rest} id={id} className={clsx(s.items, className)}>
      {children}
    </div>
  );
}

/** one thing in a list. `off`: dimmed; `over`: a drop lands here; `lift`: the one in the hand */
export function Item({
  off,
  over,
  lift,
  className,
  children,
  ...rest
}: { off?: boolean; over?: boolean; lift?: boolean } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      {...rest}
      className={clsx(s.item, off && s.itemOff, over && s.itemOver, lift && s.itemLift, className)}
    >
      {children}
    </div>
  );
}

/** the parts of an Item: its grip, its main words, a dim word, the cells at its right */
export const itemClass = {
  grip: s.itemGrip,
  main: s.itemMain,
  mono: s.itemMono,
  dim: s.itemDim,
  cells: s.itemCells,
};

/** a value the server reported (a version); amber when it needs a look */
export function Value({ id, tone, children }: { id?: string; tone?: Tone; children: ReactNode }) {
  return (
    <span id={id} className={s.value} data-tone={tone}>
      {children}
    </span>
  );
}

/** what an action said, in a box under it (an update's answer) */
export function Say({
  id,
  tone,
  hidden,
  children,
}: {
  id?: string;
  tone?: Tone;
  hidden?: boolean;
  children: ReactNode;
}) {
  return (
    <div id={id} className={s.say} data-tone={tone} hidden={hidden} role="status">
      {children}
    </div>
  );
}

/** text as the server sent it (a log), scrolling in its own box */
export function Pre({
  id,
  hidden,
  children,
}: {
  id?: string;
  hidden?: boolean;
  children: ReactNode;
}) {
  return (
    <pre id={id} className={s.pre} hidden={hidden}>
      {children}
    </pre>
  );
}

/** a line under a field: what it is for, or where to find the value */
export function FieldNote({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <div className={s.fieldNote} id={id}>
      {children}
    </div>
  );
}

/** a field with a button beside it (Auto-detect) */
export function FieldLine({ children }: { children: ReactNode }) {
  return <div className={s.fieldLine}>{children}</div>;
}

/** under the hero: which kind of media the page is showing, and the page's own action */
export function KindBar({ children }: { children: ReactNode }) {
  return <div className={s.kindbar}>{children}</div>;
}

/** the words beside the kind switch; they give way on a phone */
export function KindSay({ children }: { children: ReactNode }) {
  return <span className={s.kindSay}>{children}</span>;
}

/**
 * A value to copy somewhere else (a callback address): quiet, mono, with a
 * copy button. `label` says where it goes.
 */
export function CopyValue({ id, label, value }: { id?: string; label?: ReactNode; value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard
      ?.writeText(value)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {});
  };
  return (
    <div className={s.copy} data-copy="">
      {label ? <div className={s.copyLabel}>{label}</div> : null}
      <div className={s.copyLine}>
        <code id={id} className={s.copyText}>
          {value}
        </code>
        <button
          type="button"
          className={s.fieldBtn}
          aria-label={copied ? 'Copied' : 'Copy'}
          title={copied ? 'Copied' : 'Copy'}
          onClick={copy}
        >
          <Icon name={copied ? 'check' : 'copy'} size={14} />
        </button>
      </div>
    </div>
  );
}
