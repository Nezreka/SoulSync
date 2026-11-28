/**
 * this server's video converting, admins only: how many conversions run at
 * once, the GPU (auto, off, or one named backend), what the server found and
 * why anything is off, and a test that runs a real clip through the GPU.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  convertingKey,
  convertingQuery,
  GPU_NAMES,
  saveConverting,
  testGpu,
  type ConvertingStatus,
  type GpuMode,
} from '../../-lib/converting';
import { notifyError } from '../notice';
import { Card, Choices, Row, useSaved } from './kit';
import p from './settings.module.css';

const SLOTS = [1, 2, 3, 4, 6, 8];
const withNow = (choices: number[], v: number) =>
  choices.includes(v) ? choices : [...choices, v].sort((a, b) => a - b);

/** "NVIDIA GPU on /dev/dri/renderD128" or "the CPU" */
export function usingWords(s: ConvertingStatus): string {
  if (!s.ffmpeg) return "Nothing: SoulSync can't find ffmpeg.";
  if (s.using.key === 'cpu')
    return s.gpu === 'off' ? 'The CPU (GPU off).' : 'The CPU. No GPU worked.';
  return `${s.using.label}${s.using.device ? ` (${s.using.device})` : ''}.`;
}

function testWords(t: NonNullable<ConvertingStatus['test']>): string {
  if (!t.ok) return `Test failed on ${t.label ?? 'the GPU'}: ${t.reason || 'no reason given'}`;
  const where = t.hw_decode ? 'decoded, scaled and encoded on the GPU' : 'encoded';
  return `Test passed: a 1080p clip ${where} by ${t.label} in ${t.seconds ?? '?'}s.${t.note ? ` ${t.note}` : ''}`;
}

export function ConvertingSection() {
  const queryClient = useQueryClient();
  const onSaved = useSaved();
  const q = useQuery(convertingQuery());
  const put = (next: ConvertingStatus) => queryClient.setQueryData(convertingKey, next);
  const save = useMutation({
    mutationFn: saveConverting,
    onSuccess: (next) => {
      put(next);
      onSaved();
    },
    onError: notifyError("Couldn't save that setting"),
  });
  const test = useMutation({
    mutationFn: testGpu,
    onSuccess: put,
    onError: notifyError("Couldn't run the test"),
  });
  const s = test.data ?? q.data;
  if (q.isPending) return <p className={p.quiet}>Loading…</p>;
  if (q.isError || !s) return <p className={p.quiet}>Only an admin can change these.</p>;
  const modes = (s.modes.length ? s.modes : (['auto', 'off'] as GpuMode[])).filter(
    (m) => m === 'auto' || m === 'off' || m === s.gpu || s.tried.some((t) => t.key === m),
  );
  const problems = [
    ...s.broken.map((b) => `${b.label} is off until tested again: ${b.reason}`),
    ...s.decode_cpu.map(
      (d) => `${d.bit_depth}-bit ${d.codec.toUpperCase()} decodes on the CPU: ${d.reason}`,
    ),
  ];
  return (
    <Card>
      <Row
        label="Converting with"
        help="Video a screen can't play is converted as it streams. A GPU does it many times faster than the CPU, free."
      >
        <span className={p.tileValue}>{usingWords(s)}</span>
      </Row>
      <Row
        stacked
        label="GPU"
        help="Auto uses the first GPU that passes a test. Pick one to use only that, or turn it off."
      >
        <Choices
          label="GPU"
          options={modes.map((m) => ({ value: m, label: GPU_NAMES[m] ?? m }))}
          value={s.gpu}
          disabled={save.isPending}
          onPick={(gpu) => save.mutate({ gpu })}
        />
      </Row>
      {s.devices.length > 1 ? (
        <Row stacked label="Card" help="This server has more than one. Auto tries each.">
          <Choices
            label="Card"
            options={['auto', ...s.devices].map((d) => ({
              value: d,
              label: d === 'auto' ? 'Auto' : d.replace('/dev/dri/', ''),
            }))}
            value={s.device}
            disabled={save.isPending}
            onPick={(device) => save.mutate({ device })}
          />
        </Row>
      ) : null}
      <Row
        stacked
        label="Conversions at once"
        help={`Players and casts. Past this a screen is told the server is busy.${
          s.in_use != null ? ` ${s.in_use} running now.` : ''
        }`}
      >
        <Choices
          label="Conversions at once"
          options={withNow(SLOTS, s.slots).map((n) => ({ value: n, label: String(n) }))}
          value={s.slots}
          disabled={save.isPending}
          onPick={(slots) => save.mutate({ slots })}
        />
      </Row>
      <Row
        label="Test the GPU"
        help={
          s.test
            ? testWords(s.test)
            : 'Finds the GPU again and runs a real clip through it. Anything marked off gets another go.'
        }
      >
        <div className={p.buttons}>
          <button
            type="button"
            className={p.pill}
            data-tone="go"
            disabled={test.isPending || !s.ffmpeg}
            onClick={() => test.mutate()}
            data-nav
          >
            {test.isPending ? 'Testing…' : 'Test GPU'}
          </button>
        </div>
      </Row>
      {s.tried.length ? (
        <Row
          stacked
          label="What was tried"
          help={s.tried
            .map((t) => `${t.label}: ${t.works ? 'works' : t.reason || "didn't work"}`)
            .join(' · ')}
        >
          <span />
        </Row>
      ) : null}
      {problems.length || s.last_fallback ? (
        <Row
          stacked
          label="Fell back to the CPU"
          help={[...problems, s.last_fallback?.what].filter(Boolean).join(' · ')}
        >
          <span />
        </Row>
      ) : null}
    </Card>
  );
}
