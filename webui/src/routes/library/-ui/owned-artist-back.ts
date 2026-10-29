import { useRouter } from '@tanstack/react-router';

import {
  backButtonLabel,
  backLabelStack,
  popBackOrigin,
} from '../../artist-detail/-artist-detail.back-label';

/** dev's back button for an artist opened from somewhere else: labelled with
 *  where you came from ("back to search", "back to aphex twin") and going
 *  back there. null when the visit started inside the library, where nick's
 *  "all artists" link stays. */
export function useArrivalBack(arrived: boolean): { label: string; onBack: () => void } | null {
  const router = useRouter();
  if (!arrived) return null;
  const stack = backLabelStack();
  if (stack.length === 0 && window.history.length <= 1) return null;
  return {
    label: backButtonLabel(stack),
    onBack: () => {
      popBackOrigin();
      router.history.back();
    },
  };
}
