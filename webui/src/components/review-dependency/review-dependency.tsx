import { Notice } from '@/components/primitives';
export function ReviewDependency({ caseId }: { caseId: string }) {
  return (
    <Notice tone="warning">
      This action would conflict with an accepted album decision. Review or reopen that choice
      before trying again.{' '}
      <a href={`/review?case=${encodeURIComponent(caseId)}`}>Review the album plan →</a>
    </Notice>
  );
}
