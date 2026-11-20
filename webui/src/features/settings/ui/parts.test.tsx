/** The Settings parts: what each one does, by role and by its states. */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { REDACTED_SECRET_SENTINEL } from '../tabs/parts';
import {
  Btn,
  Card,
  ChipList,
  Chips,
  DangerRow,
  Hero,
  HeroWarn,
  ListEditor,
  Mark,
  More,
  Note,
  NumberField,
  PathField,
  Pills,
  Row,
  SecretField,
  SectionLabel,
  Segmented,
  Select,
  StatusWords,
  Switch,
  TemplateField,
  TestAction,
  TextField,
  Tile,
} from './parts';
import { Sheet } from './sheet';

describe('Btn', () => {
  it('is a button that does not submit, and is off while busy', () => {
    const onClick = vi.fn();
    render(
      <Btn icon="refresh" busy onClick={onClick}>
        Test
      </Btn>,
    );
    const button = screen.getByRole('button', { name: 'Test' });
    expect(button.getAttribute('type')).toBe('button');
    expect(button.getAttribute('aria-busy')).toBe('true');
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('Pills', () => {
  const items = [
    { key: 'sources', label: 'Sources', dot: true },
    { key: 'behaviour', label: 'Behaviour' },
    { key: 'filtering', label: 'Filtering', count: 3 },
  ];
  it('is a tablist: one selected, arrows move and wrap', () => {
    const onChange = vi.fn();
    render(<Pills label="Downloads" items={items} value="sources" onChange={onChange} />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1]);
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' });
    expect(onChange).toHaveBeenLastCalledWith('behaviour');
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowLeft' });
    expect(onChange).toHaveBeenLastCalledWith('filtering');
    fireEvent.click(tabs[2]);
    expect(onChange).toHaveBeenLastCalledWith('filtering');
  });
  it('marks a pill that needs a look, and shows a count', () => {
    render(<Pills label="Downloads" items={items} value="sources" onChange={() => undefined} />);
    expect(screen.getByTitle('Needs a look')).toBeTruthy();
    expect(screen.getByRole('tab', { name: /Filtering/ }).textContent).toBe('Filtering3');
  });
});

describe('Hero, SectionLabel, Card, Note', () => {
  it('reads the sentence and its figures', () => {
    render(
      <Hero stats={[{ value: 3, label: 'In use' }]}>
        Music tries <em>Soulseek</em> first. <HeroWarn>Qobuz isn't set up yet</HeroWarn>
      </Hero>,
    );
    expect(screen.getByText(/Music tries/).textContent).toBe(
      "Music tries Soulseek first. Qobuz isn't set up yet",
    );
    expect(screen.getByText('In use').previousSibling?.textContent).toBe('3');
  });
  it('labels a section, with its hint and action', () => {
    render(
      <SectionLabel
        title="Download order"
        hint="Tried from the top."
        action={<button>Go</button>}
      />,
    );
    expect(screen.getByRole('heading', { name: 'Download order' })).toBeTruthy();
    expect(screen.getByText('Tried from the top.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Go' })).toBeTruthy();
  });
  it('a card has a head only when it has a title', () => {
    const { rerender } = render(<Card>body</Card>);
    expect(screen.queryByRole('heading')).toBeNull();
    rerender(
      <Card title="Retry Logic" hint="What happens when a download is rejected" danger>
        body
      </Card>,
    );
    expect(screen.getByRole('heading', { name: 'Retry Logic' })).toBeTruthy();
  });
  it('a warning note is a note', () => {
    render(<Note>Heads up</Note>);
    expect(screen.getByRole('note').textContent).toBe('Heads up');
  });
});

describe('Row', () => {
  it('points its label at the control', () => {
    render(
      <Row label="Concurrent Downloads" htmlFor="max">
        <input id="max" />
      </Row>,
    );
    expect(screen.getByLabelText('Concurrent Downloads')).toBe(document.getElementById('max'));
  });
  it('shows the first sentence of long help and keeps the rest behind the i', () => {
    render(
      <Row
        label="Stream"
        help="Where previews play from. Changing it needs nothing else. It is safe."
      />,
    );
    expect(screen.getByText('Where previews play from.')).toBeTruthy();
    const i = screen.getByRole('button', { name: 'More about this setting' });
    const rest = document.getElementById(i.getAttribute('aria-controls') as string) as HTMLElement;
    expect(rest.hidden).toBe(true);
    fireEvent.click(i);
    expect(rest.hidden).toBe(false);
    expect(i.getAttribute('aria-expanded')).toBe('true');
    expect(rest.textContent).toContain('It is safe.');
  });
  it('short help has no i', () => {
    render(<Row label="Stream" help="Where previews play from." />);
    expect(screen.queryByRole('button', { name: 'More about this setting' })).toBeNull();
  });
  it('says what is wrong, marks a change, and a save', () => {
    render(<Row label="Template" error="Needs $title" changed saved />);
    expect(screen.getByRole('alert').textContent).toBe('Needs $title');
    expect(screen.getByTitle('Not saved yet')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toBe('Saved');
  });
  it('can be hidden with its control still in the page', () => {
    render(
      <Row label="Retries" id="retries-row" hidden>
        <input id="retries" />
      </Row>,
    );
    expect((document.getElementById('retries-row') as HTMLElement).hidden).toBe(true);
    expect(document.getElementById('retries')).toBeTruthy();
  });
});

describe('More', () => {
  it('starts closed, says how many and which, and keeps what is inside mounted', () => {
    render(
      <More count={2} names="timeout buffer, cleanup">
        <input id="inside" />
      </More>,
    );
    const button = screen.getByRole('button', { name: /More options/ });
    expect(button.textContent).toContain('· 2 · timeout buffer, cleanup');
    const body = document.getElementById(
      button.getAttribute('aria-controls') as string,
    ) as HTMLElement;
    expect(body.hidden).toBe(true);
    expect(document.getElementById('inside')).toBeTruthy();
    fireEvent.click(button);
    expect(body.hidden).toBe(false);
    expect(button.getAttribute('aria-expanded')).toBe('true');
  });
});

describe('Switch, Segmented, Select', () => {
  it('a switch is a checkbox with the switch role, its id and its change', () => {
    const onChange = vi.fn();
    render(<Switch id="allow-explicit" checked={false} onChange={onChange} aria-label="Allow" />);
    const box = screen.getByRole('switch') as HTMLInputElement;
    expect(box.type).toBe('checkbox');
    expect(box.id).toBe('allow-explicit');
    fireEvent.click(box);
    expect(onChange).toHaveBeenCalledTimes(1);
  });
  it('a segmented control is a radio group: click and arrows pick, off options are skipped', () => {
    const onChange = vi.fn();
    render(
      <Segmented
        id="kind"
        label="Kind"
        value="music"
        options={[
          { value: 'music', label: 'Music' },
          { value: 'video', label: 'Video', disabled: true },
          { value: 'audiobooks', label: 'Audiobooks' },
        ]}
        onChange={onChange}
      />,
    );
    const group = screen.getByRole('radiogroup', { name: 'Kind' });
    expect(group.dataset.value).toBe('music');
    expect(screen.getByRole('radio', { name: 'Music' }).getAttribute('aria-checked')).toBe('true');
    fireEvent.keyDown(group, { key: 'ArrowRight' });
    expect(onChange).toHaveBeenLastCalledWith('audiobooks');
    fireEvent.click(screen.getByRole('radio', { name: 'Audiobooks' }));
    expect(onChange).toHaveBeenCalledTimes(2);
  });
  it('a select is the browser select with its id', () => {
    const onChange = vi.fn();
    render(
      <Select
        id="stream-source"
        value="youtube"
        onChange={onChange}
        options={[
          { value: 'youtube', label: 'YouTube' },
          { value: 'active', label: 'Active' },
        ]}
      />,
    );
    const select = document.getElementById('stream-source') as HTMLSelectElement;
    expect(select.tagName).toBe('SELECT');
    fireEvent.change(select, { target: { value: 'active' } });
    expect(onChange).toHaveBeenCalled();
  });
});

describe('fields', () => {
  it('a number carries its unit inside the field', () => {
    render(<NumberField id="timeout" value="45" unit="seconds" onChange={() => undefined} />);
    const input = document.getElementById('timeout') as HTMLInputElement;
    expect(input.type).toBe('number');
    expect(input.parentElement?.textContent).toBe('seconds');
  });
  it('an invalid field says so to a screen reader', () => {
    render(<TextField id="mb" value="x" invalid onChange={() => undefined} />);
    expect(document.getElementById('mb')?.getAttribute('aria-invalid')).toBe('true');
  });

  function Secret({ start }: { start: string }) {
    const [value, setValue] = useState(start);
    return (
      <>
        <SecretField id="key" value={value} onChange={setValue} />
        <output>{value}</output>
      </>
    );
  }
  const key = () => document.getElementById('key') as HTMLInputElement;
  it('a saved secret is a mask: focus empties it, leaving it untouched keeps what is saved', () => {
    render(<Secret start={REDACTED_SECRET_SENTINEL} />);
    expect(key().type).toBe('password');
    expect(key().value).toBe(REDACTED_SECRET_SENTINEL);
    fireEvent.focus(key());
    expect(key().value).toBe('');
    fireEvent.blur(key());
    expect(key().value).toBe(REDACTED_SECRET_SENTINEL);
    expect(screen.getByRole('status').textContent).toBe(REDACTED_SECRET_SENTINEL);
    // nothing to show: the browser never had the secret
    expect(screen.queryByRole('button', { name: /what you typed/ })).toBeNull();
  });
  it('typing is a real change, an emptied field too, and the eye shows what was typed', () => {
    render(<Secret start={REDACTED_SECRET_SENTINEL} />);
    fireEvent.focus(key());
    fireEvent.change(key(), { target: { value: 'abc' } });
    expect(screen.getByRole('status').textContent).toBe('abc');
    fireEvent.click(screen.getByRole('button', { name: 'Show what you typed' }));
    expect(key().type).toBe('text');
    fireEvent.click(screen.getByRole('button', { name: 'Hide what you typed' }));
    expect(key().type).toBe('password');
    fireEvent.change(key(), { target: { value: '' } });
    fireEvent.blur(key());
    expect(screen.getByRole('status').textContent).toBe('');
  });

  it('a locked path cannot be typed in until it is unlocked, and says what its check found', () => {
    const onLock = vi.fn();
    const onChange = vi.fn();
    const { rerender } = render(
      <PathField
        id="download-path"
        value="/downloads"
        onChange={onChange}
        locked
        onLock={onLock}
        check={{ tone: 'ok', text: 'Ready. 412 GB free' }}
        inputProps={{ 'data-folder-check': 'download' }}
      />,
    );
    const input = document.getElementById('download-path') as HTMLInputElement;
    expect(input.readOnly).toBe(true);
    expect(input.dataset.folderCheck).toBe('download');
    expect(screen.getByRole('status').textContent).toBe('Ready. 412 GB free');
    expect(screen.getByRole('status').dataset.tone).toBe('ok');
    fireEvent.click(screen.getByRole('button', { name: 'Unlock' }));
    expect(onLock).toHaveBeenCalledWith(false);
    rerender(
      <PathField
        id="download-path"
        value="/downloads"
        onChange={onChange}
        locked={false}
        onLock={onLock}
      />,
    );
    expect(input.readOnly).toBe(false);
    fireEvent.change(input, { target: { value: '/music' } });
    expect(onChange).toHaveBeenCalledWith('/music');
    expect(screen.getByRole('button', { name: 'Lock' })).toBeTruthy();
  });

  it('a template shows what it saves as: folders, then the file', () => {
    const { rerender } = render(
      <TemplateField
        id="tpl"
        value="$artist/$album/$title"
        onChange={() => undefined}
        preview={{ folders: ['Burial', 'Untrue'], file: '01 Archangel.flac' }}
      />,
    );
    expect(document.querySelector('[data-template-preview]')?.textContent).toBe(
      'Saves asBurial/Untrue/01 Archangel.flac',
    );
    rerender(
      <TemplateField
        id="tpl"
        value="$x"
        onChange={() => undefined}
        invalid
        previewNote="Preview unavailable"
      />,
    );
    expect(screen.getByText('Preview unavailable')).toBeTruthy();
    expect(document.getElementById('tpl')?.getAttribute('aria-invalid')).toBe('true');
  });
});

describe('Chips, ChipList, ListEditor', () => {
  it('chips are checkboxes that toggle by key', () => {
    const onToggle = vi.fn();
    render(
      <Chips
        label="Tags"
        options={[
          { key: 'genre', label: 'Genre' },
          { key: 'mood', label: 'Mood' },
        ]}
        value={['genre']}
        onToggle={onToggle}
      />,
    );
    expect(screen.getByRole('checkbox', { name: 'Genre' }).getAttribute('aria-checked')).toBe(
      'true',
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Mood' }));
    expect(onToggle).toHaveBeenLastCalledWith('mood', true);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Genre' }));
    expect(onToggle).toHaveBeenLastCalledWith('genre', false);
  });
  it('a chip list takes one off, and says when it is empty', () => {
    const onRemove = vi.fn();
    const { rerender } = render(
      <ChipList items={['rock', 'jazz']} onRemove={onRemove} empty="No genres yet" />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Remove jazz' }));
    expect(onRemove).toHaveBeenCalledWith('jazz');
    rerender(<ChipList items={[]} onRemove={onRemove} empty="No genres yet" />);
    expect(screen.getByText('No genres yet')).toBeTruthy();
  });
  it('a list editor adds, edits and removes rows', () => {
    function Editor() {
      const [items, setItems] = useState(['/music']);
      return (
        <>
          <ListEditor
            items={items}
            onChange={setItems}
            addLabel="Add Path"
            empty="No extra folders"
            tagFor={(_, index) => (index === 0 ? 'default' : null)}
          />
          <output>{JSON.stringify(items)}</output>
        </>
      );
    }
    render(<Editor />);
    expect(screen.getByText('default')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Add Path' }));
    fireEvent.change(screen.getByLabelText('Item 2'), { target: { value: '/more' } });
    expect(screen.getByRole('status').textContent).toBe('["/music","/more"]');
    fireEvent.click(screen.getByRole('button', { name: 'Remove /music' }));
    expect(screen.getByRole('status').textContent).toBe('["/more"]');
    fireEvent.click(screen.getByRole('button', { name: 'Remove /more' }));
    expect(screen.getByText('No extra folders')).toBeTruthy();
  });
});

describe('StatusWords, Mark, TestAction, Tile, DangerRow', () => {
  it('status is words with a tone, never a dot alone', () => {
    render(<StatusWords tone="warn">Needs setup</StatusWords>);
    const st = screen.getByText('Needs setup').parentElement as HTMLElement;
    expect(st.dataset.tone).toBe('warn');
  });
  it('a mark falls back to a line icon on the brand colour when the logo fails or is missing', () => {
    const { container, rerender } = render(<Mark src="/static/img/brands/slskd.png" />);
    const img = container.querySelector('img') as HTMLImageElement;
    fireEvent.error(img);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('svg')).toBeTruthy();
    rerender(<Mark icon="magnet" brand="255, 59, 107" />);
    expect((container.firstChild as HTMLElement).style.getPropertyValue('--brand')).toBe(
      '255, 59, 107',
    );
  });
  it('a test shows its answer beside it and says Testing while it runs', () => {
    const onTest = vi.fn();
    const { rerender } = render(
      <TestAction tone="ok" said="Connected to slskd" onTest={onTest} statusId="st" />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(onTest).toHaveBeenCalled();
    expect(document.getElementById('st')?.textContent).toBe('Connected to slskd');
    expect(document.getElementById('st')?.getAttribute('aria-live')).toBe('polite');
    rerender(<TestAction tone="ok" said="Connected to slskd" busy onTest={onTest} statusId="st" />);
    expect(document.getElementById('st')?.textContent).toBe('Testing…');
    expect(document.getElementById('st')?.dataset.tone).toBe('busy');
    expect(screen.getByRole('button', { name: 'Testing…' })).toHaveProperty('disabled', true);
  });
  it('a tile is a button with its status in words', () => {
    const onClick = vi.fn();
    render(<Tile mark={<i />} name="Prowlarr" tone="ok" status="Connected" onClick={onClick} />);
    fireEvent.click(screen.getByRole('button', { name: /Prowlarr/ }));
    expect(onClick).toHaveBeenCalled();
    expect(screen.getByText('Connected')).toBeTruthy();
  });
  it('a danger row acts through its button', () => {
    const onAction = vi.fn();
    render(<DangerRow label="Clear cache" action="Clear" onAction={onAction} />);
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(onAction).toHaveBeenCalled();
  });
});

describe('Sheet', () => {
  function Host() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>opener</button>
        <Sheet
          open={open}
          onClose={() => setOpen(false)}
          title="Soulseek"
          titleId="sheet-title"
          sub="Music #1"
          bodyClass="src-modal-open"
          foot={<button>Done</button>}
        >
          <input id="first" defaultValue="" />
        </Sheet>
      </>
    );
  }
  const dialog = () => screen.getByRole('dialog', { hidden: true });

  it('stays mounted while closed, so what was typed is still there', () => {
    render(<Host />);
    expect(dialog().hidden).toBe(true);
    fireEvent.change(document.getElementById('first') as HTMLInputElement, {
      target: { value: 'kept' },
    });
    fireEvent.click(screen.getByText('opener'));
    expect(dialog().hidden).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(dialog().hidden).toBe(true);
    expect((document.getElementById('first') as HTMLInputElement).value).toBe('kept');
  });

  it('is a labelled modal dialog; open locks the page and focuses its first field', () => {
    vi.useFakeTimers();
    try {
      render(<Host />);
      screen.getByText('opener').focus();
      fireEvent.click(screen.getByText('opener'));
      expect(dialog().getAttribute('aria-modal')).toBe('true');
      expect(dialog().getAttribute('aria-labelledby')).toBe('sheet-title');
      expect(document.body.classList.contains('src-modal-open')).toBe(true);
      act(() => {
        vi.runAllTimers();
      });
      expect(document.activeElement).toBe(document.getElementById('first'));
      // Escape closes, unlocks, and gives focus back to what opened it
      fireEvent.keyDown(document.getElementById('first') as HTMLElement, { key: 'Escape' });
      expect(dialog().hidden).toBe(true);
      expect(document.body.classList.contains('src-modal-open')).toBe(false);
      expect(document.activeElement).toBe(screen.getByText('opener'));
    } finally {
      vi.useRealTimers();
    }
  });

  it('the scrim closes it, and Tab stays inside', () => {
    render(<Host />);
    fireEvent.click(screen.getByText('opener'));
    const done = screen.getByRole('button', { name: 'Done' });
    done.focus();
    fireEvent.keyDown(done, { key: 'Tab' });
    // wrapped to the first thing in the sheet (its close button)
    expect(dialog().contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(done);
    fireEvent.click(document.querySelector('[data-sheet-scrim]') as HTMLElement);
    expect(dialog().hidden).toBe(true);
  });
});
