/**
 * One place that decides whether a bitrate number is bits/s or kbit/s.
 *
 * The API is not consistent about the unit -- provider search results, the
 * mutagen probe and the catalogue columns disagree -- so the UI has to guess
 * from the magnitude. That guess was inlined at six call sites as
 * `bitrate > 5000 ? bitrate / 1000 : bitrate`, and 5,000 is too low: a 24-bit /
 * 192 kHz stereo FLAC runs 4,000-9,200 kbit/s, so every hi-res lossless file
 * fell on the wrong side and was rendered as "5 kbps" or "9 kbps" -- exactly
 * the files whose quality the user most wants to see (frontend-audit FE-08).
 *
 * 25,000 separates the two units cleanly in both directions:
 *
 *   - Nothing is legitimately above 25,000 kbit/s. Uncompressed 24/192 stereo
 *     PCM is 9,216 kbit/s and lossless compression only goes down from there;
 *     even 32-bit / 384 kHz 8-channel is under 25,000.
 *   - Nothing musical is below 25,000 bit/s. The lowest codec setting anyone
 *     ships music at is 64 kbit/s = 64,000 bit/s.
 */
const BITS_PER_SECOND_THRESHOLD = 25_000;

/** Bitrate in kbit/s, or null when there is no usable number. */
export function bitrateKbps(bitrate: number | null | undefined): number | null {
  if (!bitrate || !Number.isFinite(bitrate) || bitrate <= 0) return null;
  return bitrate > BITS_PER_SECOND_THRESHOLD ? Math.round(bitrate / 1000) : Math.round(bitrate);
}

/**
 * Codecs whose bitrate number is an AVERAGE over the file, not a setting.
 *
 * Opus and Vorbis have no constant mode worth the name; AAC and WMA store an
 * average in the header. Lossless formats vary too, but their bitrate is a
 * property of the audio rather than a quality knob — marking those would put a
 * `~` on every FLAC in the library and tell the user nothing.
 */
const VARIABLE_BITRATE_FORMATS = new Set([
  'OPUS', 'OGG', 'VORBIS', 'AAC', 'M4A', 'MP4', 'WMA',
]);

/**
 * Is this file's bitrate an average?
 *
 * Reads the catalogue's `format` column rather than parsing a file extension:
 * a path can be missing, or lie. Upstream also honours a per-file `bitrate_vbr`
 * flag so a VBR MP3 can say so; this catalogue stores no such column, and
 * guessing would mislabel the CBR majority of a typical library, so an MP3 is
 * printed as the constant it usually is.
 */
export function isVariableBitrate(format: string | null | undefined): boolean {
  if (!format) return false;
  return VARIABLE_BITRATE_FORMATS.has(String(format).trim().toUpperCase());
}

/**
 * The bitrate as the UI should show it, plus the tooltip that explains a `~`.
 *
 * `label` is null when there is no usable number, so a caller can drop the
 * segment entirely instead of printing a zero.
 */
export function formatBitrate(
  bitrate: number | null | undefined,
  format: string | null | undefined,
): { label: string | null; title: string | undefined } {
  const kbps = bitrateKbps(bitrate);
  if (kbps === null) return { label: null, title: undefined };
  if (isVariableBitrate(format)) {
    return { label: `~${kbps} kbps`, title: 'Average bitrate (VBR)' };
  }
  return { label: `${kbps} kbps`, title: undefined };
}
