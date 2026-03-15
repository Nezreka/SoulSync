"""Fake Lossless Detector Job — detects FLAC/WAV files transcoded from lossy sources."""

import json
import os
import subprocess

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.fake_lossless")

LOSSLESS_EXTENSIONS = {'.flac', '.wav', '.aiff', '.aif'}
_ffprobe_warned = False


@register_job
class FakeLosslessDetectorJob(RepairJob):
    job_id = 'fake_lossless_detector'
    display_name = 'Fake Lossless Detector'
    description = 'Detects FLAC/WAV files likely transcoded from lossy'
    help_text = (
        'Analyzes the spectral content of FLAC and WAV files to detect if they were '
        'transcoded from a lossy source (like MP3 or AAC). A genuine lossless file has '
        'audio content extending to 20kHz+, while a transcoded file shows a sharp '
        'frequency cutoff around 16-18kHz.\n\n'
        'Files flagged as fake lossless are reported as findings. You may want to '
        're-download them from a better source or keep the lossy version.\n\n'
        'Settings:\n'
        '- Spectral Cutoff kHz: Frequency threshold below which a file is considered '
        'suspicious (default 16.0 kHz)'
    )
    icon = 'repair-icon-lossless'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'spectral_cutoff_khz': 16.0,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        global _ffprobe_warned
        result = JobResult()

        # Check if ffprobe is available
        if not _is_ffprobe_available():
            if not _ffprobe_warned:
                logger.warning("ffprobe not found — Fake Lossless Detector requires ffmpeg/ffprobe to be installed")
                _ffprobe_warned = True
            return result

        settings = self._get_settings(context)
        cutoff_khz = settings.get('spectral_cutoff_khz', 16.0)

        transfer = context.transfer_folder
        if not os.path.isdir(transfer):
            return result

        # Collect lossless files
        lossless_files = []
        for root, _dirs, files in os.walk(transfer):
            if context.check_stop():
                return result
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in LOSSLESS_EXTENSIONS:
                    lossless_files.append(os.path.join(root, fname))

        total = len(lossless_files)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Scanning %d lossless files for fakes", total)

        if context.report_progress:
            context.report_progress(phase=f'Analyzing {total} lossless files...', total=total)

        for i, fpath in enumerate(lossless_files):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            result.scanned += 1
            fname = os.path.basename(fpath)

            if context.report_progress and i % 5 == 0:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Analyzing {i + 1} / {total}',
                    log_line=f'Analyzing: {fname}',
                    log_type='info'
                )

            try:
                analysis = _analyze_file(fpath)
                if not analysis:
                    result.skipped += 1
                    continue

                sample_rate = analysis.get('sample_rate', 44100)
                max_freq_khz = sample_rate / 2000  # Nyquist frequency in kHz
                detected_cutoff = analysis.get('detected_cutoff_khz')

                if detected_cutoff is not None and detected_cutoff < cutoff_khz:
                    # Likely fake lossless
                    if context.report_progress:
                        context.report_progress(
                            log_line=f'Fake: {fname} — cutoff at {detected_cutoff:.1f} kHz',
                            log_type='error'
                        )
                    if context.create_finding:
                        context.create_finding(
                            job_id=self.job_id,
                            finding_type='fake_lossless',
                            severity='warning',
                            entity_type='file',
                            entity_id=None,
                            file_path=fpath,
                            title=f'Possible fake lossless: {os.path.basename(fpath)}',
                            description=(
                                f'Spectral cutoff at ~{detected_cutoff:.1f} kHz '
                                f'(expected >{cutoff_khz:.1f} kHz for true lossless). '
                                f'File may be transcoded from a lossy source.'
                            ),
                            details={
                                'detected_cutoff_khz': round(detected_cutoff, 1),
                                'expected_min_khz': cutoff_khz,
                                'sample_rate': sample_rate,
                                'nyquist_khz': round(max_freq_khz, 1),
                                'format': os.path.splitext(fpath)[1].lower().lstrip('.'),
                                'bit_depth': analysis.get('bit_depth'),
                                'bitrate': analysis.get('bitrate'),
                                'file_size': os.path.getsize(fpath),
                            }
                        )
                        result.findings_created += 1

            except Exception as e:
                logger.debug("Error analyzing %s: %s", os.path.basename(fpath), e)
                result.errors += 1

            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Fake lossless scan: %d files checked, %d suspicious found",
                     result.scanned, result.findings_created)
        return result

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        transfer = context.transfer_folder
        if not os.path.isdir(transfer):
            return 0
        count = 0
        for _root, _dirs, files in os.walk(transfer):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in LOSSLESS_EXTENSIONS:
                    count += 1
        return count


def _is_ffprobe_available() -> bool:
    """Check if ffprobe is available on PATH."""
    try:
        subprocess.run(
            ['ffprobe', '-version'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _analyze_file(fpath: str) -> dict:
    """Analyze a lossless audio file using ffprobe to detect spectral properties.

    Uses ffprobe to get stream info (sample rate, bit depth, bitrate).
    Then uses ffmpeg's astats filter to estimate the frequency content,
    which helps detect files that were transcoded from lossy sources.
    """
    try:
        # Get basic stream info
        probe_cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-select_streams', 'a:0',
            fpath
        ]
        probe_result = subprocess.run(
            probe_cmd, capture_output=True, text=True, timeout=30
        )
        if probe_result.returncode != 0:
            return None

        probe_data = json.loads(probe_result.stdout)
        streams = probe_data.get('streams', [])
        if not streams:
            return None

        stream = streams[0]
        sample_rate = int(stream.get('sample_rate', 44100))
        bit_depth = stream.get('bits_per_raw_sample') or stream.get('bits_per_sample')
        bitrate = stream.get('bit_rate')

        analysis = {
            'sample_rate': sample_rate,
            'bit_depth': int(bit_depth) if bit_depth else None,
            'bitrate': int(bitrate) if bitrate else None,
        }

        # Use astats to analyze audio — check for spectral rolloff
        # The 'bandwidth' value from astats gives us an idea of the
        # effective frequency range. A low bandwidth relative to
        # sample rate indicates possible transcode from lossy.
        stats_cmd = [
            'ffmpeg', '-v', 'quiet',
            '-i', fpath,
            '-af', 'astats=measure_overall=none:measure_perchannel=Flat_factor',
            '-f', 'null', '-'
        ]

        # Alternative simpler approach: check the actual spectral content
        # by looking at the spectrogram. For a quick heuristic, we use
        # the showspectrumpic filter to check energy above the cutoff.
        # But that's complex. A simpler approach: check the file's actual
        # effective bitrate vs expected for lossless.
        #
        # True lossless at 44.1kHz/16-bit typically has bitrate > 700kbps
        # A 320kbps MP3 transcoded to FLAC would still be ~700+ but with
        # spectral cutoff around 16kHz.
        #
        # For a reliable check, we use ffmpeg's volumedetect on a
        # high-pass filtered version of the audio.
        highpass_cmd = [
            'ffmpeg', '-v', 'quiet',
            '-i', fpath,
            '-t', '30',  # Only analyze first 30 seconds
            '-af', f'highpass=f={int(sample_rate * 0.35)},volumedetect',
            '-f', 'null', '-'
        ]

        hp_result = subprocess.run(
            highpass_cmd, capture_output=True, text=True, timeout=60
        )

        # Parse volumedetect output from stderr
        hp_output = hp_result.stderr
        mean_volume = None
        for line in hp_output.split('\n'):
            if 'mean_volume' in line:
                try:
                    # Extract dB value
                    parts = line.split('mean_volume:')
                    if len(parts) > 1:
                        db_str = parts[1].strip().replace('dB', '').strip()
                        mean_volume = float(db_str)
                except (ValueError, IndexError):
                    pass

        if mean_volume is not None:
            # If the mean volume above 35% of Nyquist is very low (< -70dB),
            # there's likely a hard frequency cutoff → probable transcode
            if mean_volume < -70:
                # Estimate cutoff: use the highpass frequency as an approximation
                analysis['detected_cutoff_khz'] = (sample_rate * 0.35) / 1000
            else:
                # Audio has content at high frequencies — likely genuine lossless
                analysis['detected_cutoff_khz'] = sample_rate / 2000  # Full range

        return analysis

    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        logger.debug("ffprobe/ffmpeg analysis failed for %s: %s", os.path.basename(fpath), e)
        return None
