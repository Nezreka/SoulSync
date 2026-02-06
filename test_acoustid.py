#!/usr/bin/env python3
"""
AcoustID Integration Test Script

Run this script to test the AcoustID verification system before using it in production.
It will check:
1. fpcalc binary availability
2. API key validation
3. Fingerprint generation (if audio file provided)
4. Full verification flow (if audio file and expected track info provided)

Usage:
    python test_acoustid.py                          # Basic tests
    python test_acoustid.py path/to/audio.mp3        # Test with audio file
    python test_acoustid.py path/to/audio.mp3 "Song Title" "Artist Name"  # Full test
"""

import sys
import os
import io

# Fix Windows encoding issues
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path


def print_header(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def print_result(success, message):
    icon = "[PASS]" if success else "[FAIL]"
    print(f"  {icon} {message}")


def test_chromaprint():
    """Test if chromaprint/fpcalc is available for fingerprinting."""
    print_header("Testing fingerprint backend availability")

    from core.acoustid_client import CHROMAPRINT_AVAILABLE, ACOUSTID_AVAILABLE, FPCALC_PATH

    if not ACOUSTID_AVAILABLE:
        print_result(False, "pyacoustid library not installed!")
        print("\n  To install:")
        print("    pip install pyacoustid")
        return False

    if CHROMAPRINT_AVAILABLE and FPCALC_PATH:
        print_result(True, f"fpcalc ready: {FPCALC_PATH}")
        return True

    if CHROMAPRINT_AVAILABLE:
        print_result(True, "Fingerprint backend available")
        return True

    print_result(False, "No fingerprint backend available!")
    print("\n  fpcalc will be auto-downloaded on first use.")
    print("  Or manually install:")
    print("    - Windows: Auto-download supported")
    print("    - macOS: brew install chromaprint")
    print("    - Linux: apt install libchromaprint-tools")
    return False


def test_api_key():
    """Test if AcoustID API key is configured and valid."""
    print_header("Testing AcoustID API key")

    from core.acoustid_client import AcoustIDClient
    from config.settings import config_manager

    api_key = config_manager.get('acoustid.api_key', '')

    if not api_key:
        print_result(False, "No API key configured in settings")
        print("\n  To configure:")
        print("    1. Get a free API key from https://acoustid.org/new-application")
        print("    2. Add it in Settings > AcoustID section")
        return False

    print(f"  API key found: {api_key[:8]}...{api_key[-4:]}")

    client = AcoustIDClient()
    success, message = client.test_api_key()

    print_result(success, message)
    return success


def test_enabled():
    """Test if AcoustID verification is enabled."""
    print_header("Testing AcoustID enabled status")

    from config.settings import config_manager

    enabled = config_manager.get('acoustid.enabled', False)

    if enabled:
        print_result(True, "AcoustID verification is ENABLED")
    else:
        print_result(False, "AcoustID verification is DISABLED")
        print("\n  To enable:")
        print("    1. Go to Settings > AcoustID section")
        print("    2. Check 'Enable Download Verification'")

    return enabled


def test_availability():
    """Test overall availability."""
    print_header("Testing overall availability")

    from core.acoustid_client import AcoustIDClient

    client = AcoustIDClient()
    available, reason = client.is_available()

    print_result(available, reason)
    return available


def test_fingerprint_and_lookup(audio_file):
    """Test fingerprint generation and AcoustID lookup for an audio file."""
    print_header(f"Testing fingerprint and AcoustID lookup")
    print(f"  File: {audio_file}")

    if not os.path.isfile(audio_file):
        print_result(False, f"File not found: {audio_file}")
        return None

    from core.acoustid_client import AcoustIDClient

    client = AcoustIDClient()

    available, reason = client.is_available()
    if not available:
        print_result(False, f"AcoustID not available: {reason}")
        return None

    print("  Fingerprinting and looking up (this may take a moment)...")
    result = client.fingerprint_and_lookup(audio_file)

    if result:
        recordings = result.get('recordings', [])
        score = result.get('best_score', 0)
        print_result(True, f"Found {len(recordings)} recording(s) (score: {score:.2f})")

        for i, rec in enumerate(recordings[:5]):  # Show first 5
            title = rec.get('title', '?')
            artist = rec.get('artist', '?')
            mbid = rec.get('mbid', '?')
            rec_score = rec.get('score', 0)
            print(f"    {i+1}. \"{title}\" by {artist} (score: {rec_score:.2f})")
            print(f"       https://musicbrainz.org/recording/{mbid}")

        if len(recordings) > 5:
            print(f"    ... and {len(recordings) - 5} more")

        return result
    else:
        print_result(False, "Track not found in AcoustID database")
        print("  This may be a rare/new track not yet fingerprinted.")
        return None


def test_musicbrainz_lookup(track_name, artist_name):
    """Test MusicBrainz lookup for expected track."""
    print_header("Testing MusicBrainz lookup")
    print(f"  Track: '{track_name}'")
    print(f"  Artist: '{artist_name}'")

    try:
        from database.music_database import MusicDatabase
        from core.musicbrainz_service import MusicBrainzService

        db = MusicDatabase()
        mb_service = MusicBrainzService(db)

        print("  Searching MusicBrainz...")
        result = mb_service.match_recording(track_name, artist_name)

        if result:
            mbid = result.get('mbid')
            confidence = result.get('confidence', 0)
            cached = result.get('cached', False)

            print_result(True, f"Found match (confidence: {confidence}%)")
            print(f"    MBID: {mbid}")
            print(f"    https://musicbrainz.org/recording/{mbid}")
            print(f"    Cached: {cached}")
            return result
        else:
            print_result(False, "No match found in MusicBrainz")
            return None

    except Exception as e:
        print_result(False, f"Error: {e}")
        return None


def test_full_verification(audio_file, track_name, artist_name):
    """Test the full verification flow."""
    print_header("Testing full verification flow")
    print(f"  File: {audio_file}")
    print(f"  Expected: '{track_name}' by '{artist_name}'")

    from core.acoustid_verification import AcoustIDVerification, VerificationResult

    verifier = AcoustIDVerification()

    # Check availability first
    available, reason = verifier.quick_check_available()
    if not available:
        print_result(False, f"Verification not available: {reason}")
        return

    print("  Running verification (this may take a moment)...")
    result, message = verifier.verify_audio_file(
        audio_file,
        track_name,
        artist_name
    )

    if result == VerificationResult.PASS:
        print_result(True, f"VERIFICATION PASSED: {message}")
    elif result == VerificationResult.FAIL:
        print_result(False, f"VERIFICATION FAILED: {message}")
    elif result == VerificationResult.SKIP:
        print(f"  [SKIP] Verification skipped: {message}")
    else:
        print(f"  [????] Unknown result: {result.value} - {message}")


def main():
    print("\n" + "=" * 60)
    print("  ACOUSTID VERIFICATION SYSTEM TEST")
    print("=" * 60)

    # Parse arguments
    audio_file = sys.argv[1] if len(sys.argv) > 1 else None
    track_name = sys.argv[2] if len(sys.argv) > 2 else None
    artist_name = sys.argv[3] if len(sys.argv) > 3 else None

    # Run basic tests
    chromaprint_ok = test_chromaprint()
    api_key_ok = test_api_key()
    enabled_ok = test_enabled()
    available_ok = test_availability()

    # Summary of basic tests
    print_header("Basic Tests Summary")
    print(f"  Chromaprint: {'OK' if chromaprint_ok else 'MISSING'}")
    print(f"  API key:     {'OK' if api_key_ok else 'MISSING/INVALID'}")
    print(f"  Enabled:     {'YES' if enabled_ok else 'NO'}")
    print(f"  Available:   {'YES' if available_ok else 'NO'}")

    if not audio_file:
        print("\n" + "-" * 60)
        print("  To test fingerprinting, provide an audio file:")
        print("    python test_acoustid.py path/to/audio.mp3")
        print("\n  To test full verification flow:")
        print("    python test_acoustid.py path/to/audio.mp3 \"Song Title\" \"Artist\"")
        print("-" * 60)
        return

    # Test with audio file (combined fingerprint + lookup)
    lookup_result = test_fingerprint_and_lookup(audio_file)

    if track_name and artist_name:
        # Test MusicBrainz lookup
        mb_result = test_musicbrainz_lookup(track_name, artist_name)

        # Test full verification
        if available_ok:
            test_full_verification(audio_file, track_name, artist_name)
        else:
            print("\n  Skipping full verification test (not available)")

    # Point to log file
    print("\n" + "-" * 60)
    log_path = Path(__file__).parent / "logs" / "acoustid.log"
    print(f"  Detailed logs: {log_path}")
    print("-" * 60 + "\n")


if __name__ == "__main__":
    main()
