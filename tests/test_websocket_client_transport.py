from pathlib import Path


def test_websocket_client_prefers_polling_before_websocket():
    script_path = Path(__file__).resolve().parents[1] / "webui" / "static" / "script.js"
    script = script_path.read_text()

    assert "transports: ['polling', 'websocket']" in script
