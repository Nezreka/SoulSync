/** stagger for a card's fade-in, capped so a long page doesn't trail off (dev's curve) */
function cardDelay(index: number): number {
  return Math.min(index, 20) * 30;
}

/** the card fade-in. backwards, not both: a fill that outlives the animation
 *  holds the transform and the hover lift never shows */
export function cardFadeIn(index: number): string {
  return `cardFadeIn 0.35s cubic-bezier(0.4,0,0.2,1) ${cardDelay(index)}ms backwards`;
}
