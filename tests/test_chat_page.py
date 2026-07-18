"""Chat P2 — the Chat page wiring across both sidebars (pin tests).

One #chat-page div serves the whole app: the music nav shows it directly, the
video nav reveals the SAME div via SHARED_PAGES. These pins hold the seams
together (nav entries, deep links, page registration, poll gating, and the
remote-username attribute escaping).
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
_CHAT_JS = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8", errors="replace")
_INIT_JS = (_ROOT / "webui" / "static" / "init.js").read_text(encoding="utf-8", errors="replace")
_VSIDE_JS = (_ROOT / "webui" / "static" / "video" / "video-side.js").read_text(
    encoding="utf-8", errors="replace")
_VSIDE_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(
    encoding="utf-8", errors="replace")


class TestNavAndPage:
    def test_nav_entries_in_both_system_sections(self):
        assert 'data-page="chat" href="/chat"' in _HTML
        assert 'data-video-page="video-chat" href="/video-chat"' in _HTML
        assert 'id="chat-nav-badge"' in _HTML          # unread badge slots (P3)
        assert 'id="video-chat-nav-badge"' in _HTML

    def test_one_page_div_and_script_included(self):
        assert _HTML.count('id="chat-page"') == 1     # ONE div, two sidebars
        assert "filename='chat.js'" in _HTML
        for hook in ("data-chat-rooms", "data-chat-convos", "data-chat-messages",
                     "data-chat-composer", "data-chat-users", "data-chat-input"):
            assert hook in _HTML, f"missing hook {hook}"


class TestRouting:
    def test_music_deeplink_and_loader(self):
        assert "'chat'" in _INIT_JS.split("_DEEPLINK_VALID_PAGES")[1][:400]
        assert "case 'chat':" in _INIT_JS
        assert "window.ChatPage.open()" in _INIT_JS

    def test_video_side_shares_the_music_page(self):
        assert "'video-chat': 'chat'" in _VSIDE_JS     # SHARED_PAGES mapping
        assert "{ id: 'video-chat', label: 'Chat', shared: true }" in _VSIDE_JS
        # CSS reveal: the video nav shows the music #chat-page, hides the host
        assert 'body[data-side="video"][data-video-page="video-chat"] #chat-page' in _VSIDE_CSS
        assert ('body[data-side="video"][data-video-page="video-chat"] #video-page-host'
                in _VSIDE_CSS)

    def test_react_manifest_knows_chat(self):
        ts = (_ROOT / "webui" / "src" / "platform" / "shell" / "route-manifest.ts").read_text(
            encoding="utf-8", errors="replace")
        assert "{ pageId: 'chat', path: '/chat', kind: 'legacy' }" in ts


class TestChatModule:
    def test_poll_gate_works_on_both_sides(self):
        # NO .active check — the video side reveals the page by CSS alone, so
        # visibility must come from computed layout, not the music-side class.
        assert "page.offsetParent !== null && !document.hidden" in _CHAT_JS
        assert "classList.contains('active') &&" not in _CHAT_JS.split("function pageVisible")[1].split("}")[0]
        assert "document.hidden" in _CHAT_JS

    def test_remote_usernames_are_attribute_escaped(self):
        # Soulseek usernames are remote input; esc() leaves double quotes intact,
        # so every attribute interpolation must go through attr().
        assert "function attr(s)" in _CHAT_JS
        assert '&quot;' in _CHAT_JS
        assert "data-chat-user=\"' + attr(" in _CHAT_JS
        assert "data-chat-open-pm=\"' + attr(" in _CHAT_JS
        assert "data-chat-user=\"' + esc(" not in _CHAT_JS
        assert "data-chat-open-pm=\"' + esc(" not in _CHAT_JS

    def test_video_page_event_starts_and_stops_polling(self):
        assert "soulsync:video-page-shown" in _CHAT_JS
        assert "e.detail !== 'video-chat') stopPolling()" in _CHAT_JS

    def test_read_only_composer_when_sending_gated(self):
        assert "Read-only" in _CHAT_JS
        assert "input.disabled = !state.canSend" in _CHAT_JS
