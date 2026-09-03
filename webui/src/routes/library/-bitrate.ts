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
 * Magnitude alone cannot settle it in general, because the two ranges really do
 * overlap: 64 kbit/s is 64,000 bit/s, and a 24-bit / 192 kHz 5.1 stream is
 * 27,648 kbit/s. What separates them is the FORMAT, which every call site
 * already has:
 *
 *   - Only a LOSSLESS codec can carry a five-figure kbit/s number, and it takes
 *     multichannel or DSD to get there: 24/192 5.1 PCM is 27,648 kbit/s, DSD512
 *     stereo is 45,158, and 32-bit / 384 kHz 8-channel is 98,304. (The comment
 *     this replaces claimed that last one was "under 25,000" -- it is off by a
 *     factor of four, which is how the threshold came to truncate exactly the
 *     files it was raised to protect.) Below 150,000 those stay kbit/s; a real
 *     lossless bit/s number starts around 200,000 (a 200 kbit/s FLAC), so
 *     nothing legitimate lands between the two.
 *   - Everything else -- lossy, ALAC-or-AAC-in-m4a, an unlabelled row -- is
 *     stereo-shaped: nothing musical exceeds ~10,000 kbit/s, and nothing
 *     musical drops below 25,000 bit/s (the lowest rate anyone ships music at
 *     is 32 kbit/s = 32,000 bit/s).
 */

/** Above this many units, a value of THIS format's shape must be bit/s. */
const BITS_PER_SECOND_THRESHOLD = 25_000;
const LOSSLESS_BITS_PER_SECOND_THRESHOLD = 150_000;

/**
 * Formats that can legitimately report a five-figure kbit/s number.
 *
 * Matched as a substring because the value is not always a bare format token:
 * a search result's `quality` is free text ("FLAC 24bit", "flac/wav"), while a
 * catalogue row's `format` is a clean extension. m4a/mp4 and wma are
 * deliberately NOT here -- each can hold either a lossless or a lossy codec,
 * and their lossless side is stereo-shaped anyway, so the ordinary threshold
 * reads both correctly.
 */
const LOSSLESS_FORMAT_PATTERN =
  /flac|alac|wavpack|aiff|\baif\b|\bwave?\b|\bwv\b|\bape\b|\bpcm\b|dsd|\bdsf\b|\bdff\b/;

function unitThreshold(format: string | null | undefined): number {
  const f = String(format ?? '').trim().toLowerCase();
  if (f && LOSSLESS_FORMAT_PATTERN.test(f)) return LOSSLESS_BITS_PER_SECOND_THRESHOLD;
  return BITS_PER_SECOND_THRESHOLD;
}

/**
 * Bitrate in kbit/s, or null when there is no usable number.
 *
 * `format` is optional only so a caller that genuinely has none can omit it --
 * pass it whenever the row carries one, or a hi-res multichannel file is read
 * as a 28 kbps one.
 */
export function bitrateKbps(
  bitrate: number | null | undefined,
  format?: string | null,
): number | null {
  if (!bitrate || !Number.isFinite(bitrate) || bitrate <= 0) return null;
  return bitrate > unitThreshold(format) ? Math.round(bitrate / 1000) : Math.round(bitrate);
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
  const kbps = bitrateKbps(bitrate, format);
  if (kbps === null) return { label: null, title: undefined };
  if (isVariableBitrate(format)) {
    return { label: `~${kbps} kbps`, title: 'Average bitrate (VBR)' };
  }
  return { label: `${kbps} kbps`, title: undefined };
}
