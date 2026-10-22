/**
 * The setup flow's root: one full-screen layer, the current step, and the
 * work every session starts with (where are we running, what's on the
 * network). The preview gets a tag in the corner the whole time.
 */

import { useEffect } from 'react';

import { discoverServices, fetchEnvironment } from './api';
import { emptyDraft } from './plan';
import { AccountStep } from './steps/account';
import { FoldersStep } from './steps/folders';
import { LibraryStep } from './steps/library';
import { MetadataStep } from './steps/metadata';
import { PurposeStep } from './steps/purpose';
import { ReadyStep } from './steps/ready';
import { ServerStep } from './steps/server';
import { SourcesStep } from './steps/sources';
import { VideoStep } from './steps/video';
import { WelcomeStep } from './steps/welcome';
import { useOnboarding } from './store';
import { s } from './ui';

export function Onboarding() {
  const open = useOnboarding((st) => st.open);
  const step = useOnboarding((st) => st.step);
  const session = useOnboarding((st) => st.session);
  const preview = useOnboarding((st) => st.preview);
  const pretend = useOnboarding((st) => st.pretend);
  const close = useOnboarding((st) => st.close);

  useEffect(() => {
    if (!open) return;
    let live = true;
    const { setEnv, setFound, update } = useOnboarding.getState();
    void fetchEnvironment(pretend === 'docker' || pretend === 'bare' ? pretend : undefined)
      .then((env) => {
        if (!live) return;
        setEnv(env);
        // a brand-new draft takes the environment's folders straight away
        update((d) => {
          const blank = emptyDraft();
          for (const key of Object.keys(env.folders) as (keyof typeof env.folders)[])
            if (!d.folders[key] || d.folders[key] === blank.folders[key])
              d.folders[key] = env.folders[key];
        });
      })
      .catch(() => undefined);
    if (pretend === 'empty') {
      setFound({}, false);
    } else {
      setFound({}, true);
      void discoverServices()
        .catch(() => ({}))
        .then((found) => live && setFound(found, false));
    }
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  const finished = () => close();

  let body;
  switch (step) {
    case 'welcome':
      body = <WelcomeStep onFinished={finished} />;
      break;
    case 'account':
      body = <AccountStep />;
      break;
    case 'purpose':
      body = <PurposeStep />;
      break;
    case 'server':
      body = <ServerStep />;
      break;
    case 'folders':
      body = <FoldersStep />;
      break;
    case 'sources':
      body = <SourcesStep />;
      break;
    case 'metadata':
      body = <MetadataStep />;
      break;
    case 'video':
      body = <VideoStep />;
      break;
    case 'library':
      body = <LibraryStep />;
      break;
    default:
      body = <ReadyStep onFinished={finished} />;
  }

  return (
    <div
      className={s.overlay}
      role="dialog"
      aria-modal="true"
      aria-label={preview ? 'SoulSync setup preview' : 'SoulSync setup'}
      data-onboarding-step={step}
    >
      {body}
      {preview ? (
        <div className={s.previewTag} role="status">
          Preview, nothing is saved
          <button type="button" onClick={finished}>
            Close
          </button>
        </div>
      ) : null}
    </div>
  );
}
