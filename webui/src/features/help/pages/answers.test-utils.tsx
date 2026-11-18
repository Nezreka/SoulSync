/**
 * Renders a page's answers the way home.tsx does (a details per answer,
 * the title in its summary), so the page tests read what a person reads.
 */

import { render } from '@testing-library/react';

import type { PageAnswer, UseAnswers } from './index';

function Answers({ use, isAdmin, page }: { use: UseAnswers; isAdmin: boolean; page: string }) {
  const answers = use({ page, isAdmin });
  return (
    <div data-testid="answers" data-count={answers.length}>
      {answers.map((a) => (
        <details key={a.id} data-id={a.id} data-tone={a.tone} open={a.open}>
          <summary>
            {a.title}
            {a.tone === 'warn' ? ' [Needs you]' : a.live ? ' [Live]' : ''}
          </summary>
          <div>{a.body}</div>
        </details>
      ))}
    </div>
  );
}

export function renderAnswers(use: UseAnswers, { isAdmin = true, page = 'page' } = {}) {
  return render(<Answers use={use} isAdmin={isAdmin} page={page} />);
}

/** the answer titles in order */
export function titles(container: HTMLElement): string[] {
  return [...container.querySelectorAll('summary')].map((el) =>
    (el.textContent || '').replace(/ \[(Live|Needs you)\]$/, ''),
  );
}

export function answer(container: HTMLElement, id: string): HTMLElement {
  const el = container.querySelector<HTMLElement>(`details[data-id="${id}"]`);
  if (!el) throw new Error(`no answer ${id}; have ${titles(container).join(' | ')}`);
  return el;
}

export function asProfile(profileId: number, isAdmin: boolean) {
  window.getCurrentProfileContext = () => ({ profileId, isAdmin });
}

export type { PageAnswer };
