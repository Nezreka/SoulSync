from core.soulseek_client import SoulseekClient


def test_slskd_direct_resolution_reaches_search_results():
    client = SoulseekClient.__new__(SoulseekClient)
    file = {'filename': 'Son 2 Sea Ver.flac', 'size': 74_931_241,
            'length': 238, 'bitRate': 1411, 'sampleRate': 48_000, 'bitDepth': 24}
    searched, _ = client._process_search_responses([{'username': 'fishingpvalues', 'files': [file]}])
    browsed = client.parse_browse_results_to_tracks('fishingpvalues', [file])
    assert [(r.bitrate, r.sample_rate, r.bit_depth) for r in searched + browsed] == [
        (1411, 48_000, 24), (1411, 48_000, 24)]
