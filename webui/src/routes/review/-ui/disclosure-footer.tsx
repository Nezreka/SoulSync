import { Button } from '@/components/form/form';

import styles from './review-page.module.css';

export function DisclosureFooter({
  expanded,
  onToggle,
  more,
  fewer,
}: {
  expanded: boolean;
  onToggle: () => void;
  more: string;
  fewer: string;
}) {
  return (
    <div className={styles.disclosureFooter}>
      <Button type="button" aria-expanded={expanded} onClick={onToggle}>
        {expanded ? fewer : more}
        <span aria-hidden="true">{expanded ? '⌃' : '⌄'}</span>
      </Button>
    </div>
  );
}
