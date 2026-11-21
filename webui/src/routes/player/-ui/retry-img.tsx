/**
 * a picture from the library's artwork route, which answers "not yet" (a
 * 404 marked pending) the first time an album's art isn't cached and goes
 * to fetch it. a plain <img> gives up there and stays empty; this asks again
 * a couple of times while it's being fetched. anything else that fails is
 * hidden instead of showing a broken image.
 */

import { useEffect, useState, type ImgHTMLAttributes } from 'react';

import { canRetryArt, retryArt } from '../-lib/images';

const WAITS = [1500, 4000, 9000];

export function RetryImg({ src, ...rest }: ImgHTMLAttributes<HTMLImageElement> & { src: string }) {
  const [tries, setTries] = useState(0);
  const [gone, setGone] = useState(false);
  useEffect(() => {
    setTries(0);
    setGone(false);
  }, [src]);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (!pending) return;
    const t = window.setTimeout(() => {
      setPending(false);
      setTries((n) => n + 1);
    }, WAITS[tries] ?? 9000);
    return () => window.clearTimeout(t);
  }, [pending, tries]);
  if (gone) return null;
  return (
    <img
      {...rest}
      src={tries ? retryArt(src, tries) : src}
      onError={() => {
        if (canRetryArt(src) && tries < WAITS.length) setPending(true);
        else setGone(true);
      }}
    />
  );
}
