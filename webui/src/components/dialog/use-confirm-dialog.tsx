import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

import { Button } from '@/components/form/form';

import { DialogFrame, DialogHeader, DialogBody, DialogFooter } from './dialog';
interface Confirmation {
  title: string;
  message: ReactNode;
  confirmText: string;
  destructive?: boolean;
}
/** A single accessible confirmation, cancelled if its owner leaves the page. */
export function useConfirmDialog() {
  const [request, setRequest] = useState<Confirmation | null>(null);
  const resolve = useRef<((value: boolean) => void) | null>(null);
  const settle = useCallback((value: boolean) => {
    resolve.current?.(value);
    resolve.current = null;
    setRequest(null);
  }, []);
  useEffect(
    () => () => {
      resolve.current?.(false);
    },
    [],
  );
  const confirm = useCallback(
    (next: Confirmation) =>
      new Promise<boolean>((done) => {
        resolve.current?.(false);
        resolve.current = done;
        setRequest(next);
      }),
    [],
  );
  const dialog = request ? (
    <DialogFrame
      open
      onOpenChange={(open) => {
        if (!open) settle(false);
      }}
    >
      <DialogHeader title={request.title} />
      <DialogBody>
        <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{request.message}</div>
      </DialogBody>
      <DialogFooter>
        <Button onClick={() => settle(false)}>Cancel</Button>
        <Button variant={request.destructive ? 'danger' : 'primary'} onClick={() => settle(true)}>
          {request.confirmText}
        </Button>
      </DialogFooter>
    </DialogFrame>
  ) : null;
  return { confirm, dialog };
}
