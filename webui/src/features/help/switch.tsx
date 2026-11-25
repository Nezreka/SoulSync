/** the on/off switch Report and Logs use (role switch, read out with its label). the
 *  button is the touch target, the track inside is what you see */

import a from './admin.module.css';

export function Switch({
  label,
  on,
  onChange,
}: {
  label: string;
  on: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      className={a.switch}
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
    >
      <span className={a.track} aria-hidden="true" />
    </button>
  );
}
