/**
 * access requests (#2304): a member asked for a feature that isn't switched
 * on for them (Podcasts, YouTube channels, Discover...). an admin answers
 * here; approving switches that one feature on for that person, through the
 * same save the People editor uses, so the server's policy still decides.
 *
 * shown above the requests list, and only when someone is waiting.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Button } from '@/components/form';

import styles from './requests-page.module.css';

interface AccessRequest {
  id: number;
  requester_id: number;
  requester_name: string | null;
  feature: string;
  label: string;
  created_at: string | null;
}

const KEY = ['requests', 'access'] as const;

async function fetchAccessRequests(): Promise<AccessRequest[]> {
  const res = await fetch('/api/feature-requests', { headers: { Accept: 'application/json' } });
  if (!res.ok) return [];
  const data = (await res.json()) as { pending?: AccessRequest[] };
  return data.pending ?? [];
}

async function answer(id: number, yes: boolean): Promise<void> {
  const res = await fetch(`/api/feature-requests/${id}/${yes ? 'approve' : 'decline'}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  const data = (await res.json().catch(() => ({}))) as { success?: boolean; error?: string };
  if (!res.ok || !data.success) throw new Error(data.error || 'Could not answer that request');
}

export function AccessRequests() {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: KEY, queryFn: fetchAccessRequests, staleTime: 30_000 });
  const act = useMutation({
    mutationFn: ({ id, yes }: { id: number; yes: boolean }) => answer(id, yes),
    onSuccess: (_d, { yes }) =>
      window.showToast?.(yes ? 'Switched on for them' : 'Declined', yes ? 'success' : 'info'),
    onError: (e: Error) => window.showToast?.(e.message, 'error'),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: KEY }),
  });
  const rows = list.data ?? [];
  if (!rows.length) return null;
  return (
    <section aria-label="Access requests">
      <ul className={styles.list}>
        {rows.map((r) => (
          <li key={r.id} className={styles.row} data-status="pending">
            <div className={styles.thumb} aria-hidden="true">
              <span className={styles.thumbGlyph}>✦</span>
            </div>
            <div className={styles.main}>
              <div className={styles.titleLine}>
                <span className={styles.rowTitle}>{r.label}</span>
                <span className={styles.kind}>Access</span>
              </div>
              <div className={styles.who}>
                {r.requester_name || 'Someone'} asked for this to be switched on for them
              </div>
            </div>
            <div className={styles.side}>
              <Button
                variant="primary"
                size="sm"
                disabled={act.isPending}
                aria-label={`Switch on ${r.label} for ${r.requester_name || 'them'}`}
                onClick={() => act.mutate({ id: r.id, yes: true })}
              >
                Approve
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={act.isPending}
                aria-label={`Decline ${r.label} for ${r.requester_name || 'them'}`}
                onClick={() => act.mutate({ id: r.id, yes: false })}
              >
                Decline
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
