from pathlib import Path


def test_websocket_client_prefers_polling_before_websocket():
    script_path = Path(__file__).resolve().parents[1] / "webui" / "static" / "core.js"
    script = script_path.read_text(encoding="utf-8")

    assert "transports: ['polling', 'websocket']" in script
