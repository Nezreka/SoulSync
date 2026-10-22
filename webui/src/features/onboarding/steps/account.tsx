/** Step: the admin profile and how SoulSync gets unlocked. */

import type { LockMode } from '../types';

import { PROFILE_COLORS } from '../plan';
import { useOnboarding } from '../store';
import { Field, Option, s, StepFrame } from '../ui';

const LOCKS: { id: LockMode; title: string; desc: string }[] = [
  { id: 'open', title: 'Leave it open', desc: 'Fine on a home network only you can reach.' },
  { id: 'pin', title: 'Lock with a PIN', desc: 'One PIN for the admin. Good for a home server.' },
  {
    id: 'password',
    title: 'Usernames and passwords',
    desc: 'Each person signs in. Use this if it faces the internet.',
  },
];

const COLOR_NAMES = ['Green', 'Violet', 'Sky', 'Amber', 'Rose', 'Silver'];

export function AccountStep() {
  const account = useOnboarding((st) => st.draft.account);
  const update = useOnboarding((st) => st.update);
  const initial = account.name.trim().charAt(0).toUpperCase() || 'S';

  return (
    <StepFrame
      step="account"
      title="Make it yours first"
      lead="This is the admin profile. It can change every setting and invite everyone else."
    >
      <section className={s.inputRow} style={{ gap: 20, flexWrap: 'wrap' }}>
        <div className={s.avatar} style={{ background: account.color }} aria-hidden="true">
          {initial}
        </div>
        <div style={{ flex: '1 1 260px', maxWidth: 360 }}>
          <Field
            id="ob-name"
            label="Your name"
            value={account.name}
            autoComplete="nickname"
            placeholder="Your name"
            maxLength={40}
            onChange={(e) => update((d) => void (d.account.name = e.target.value))}
            autoFocus
          />
        </div>
        <div
          className={s.swatches}
          role="radiogroup"
          aria-label="Profile colour"
          style={{ paddingBottom: 8 }}
        >
          {PROFILE_COLORS.map((c, i) => (
            <button
              key={c}
              type="button"
              role="radio"
              aria-checked={account.color === c}
              aria-label={COLOR_NAMES[i]}
              className={s.swatch}
              style={{ background: c }}
              onClick={() => update((d) => void (d.account.color = c))}
            />
          ))}
        </div>
      </section>

      <section className={s.section}>
        <h2 className={s.h2}>How should SoulSync be unlocked?</h2>
        <div className={s.grid3} role="radiogroup" aria-label="How SoulSync is unlocked">
          {LOCKS.map((l) => (
            <Option
              key={l.id}
              title={l.title}
              desc={l.desc}
              checked={account.lock === l.id}
              onSelect={() => update((d) => void (d.account.lock = l.id))}
            />
          ))}
        </div>
        {account.lock === 'pin' ? (
          <div className={s.inputRow} style={{ alignItems: 'center', gap: 16, marginTop: 6 }}>
            <Field
              id="ob-pin"
              label="Choose a PIN"
              type="password"
              inputMode="numeric"
              autoComplete="new-password"
              maxLength={8}
              value={account.pin}
              className={s.pinInput}
              onChange={(e) =>
                update((d) => void (d.account.pin = e.target.value.replace(/\D/g, '')))
              }
            />
          </div>
        ) : null}
        {account.lock === 'password' ? (
          <div
            style={{
              maxWidth: 360,
              marginTop: 6,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <Field
              id="ob-password"
              label="Password"
              type="password"
              autoComplete="new-password"
              value={account.password}
              onChange={(e) => update((d) => void (d.account.password = e.target.value))}
            />
            <p className={s.note}>
              You sign in as {account.name.trim() || 'your name'}. Everyone you invite gets their
              own.
            </p>
          </div>
        ) : null}
        <p className={s.note}>
          Behind Authelia, Authentik or oauth2-proxy? You can trust its header later in Settings,
          Security.
        </p>
      </section>
    </StepFrame>
  );
}
