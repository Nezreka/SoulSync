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
        # Soulseek usernames are remote input. esc() is now a pure string
        # escaper covering BOTH contexts (quotes included) and attr aliases it
        # — attribute interpolations stay safe whichever name they use.
        assert "var attr = esc" in _CHAT_JS
        assert ".replace(/\"/g, '&quot;')" in _CHAT_JS
        assert "data-chat-user=\"' + attr(" in _CHAT_JS
        assert "data-chat-open-pm=\"' + attr(" in _CHAT_JS

    def test_video_page_event_starts_and_stops_polling(self):
        assert "soulsync:video-page-shown" in _CHAT_JS
        assert "e.detail !== 'video-chat') stopPolling()" in _CHAT_JS

    def test_read_only_composer_when_sending_gated(self):
        assert "Read-only" in _CHAT_JS
        assert "input.disabled = !state.canSend" in _CHAT_JS


class TestPush:
    """P3 — socket push: badges + PM toasts without the page open."""

    def test_server_loop_is_idle_gated_and_baselines(self):
        ws = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="replace")
        loop = ws.split("def _emit_chat_push_loop")[1].split("\ndef ")[0]
        assert "_has_connected_clients()" in loop        # zero slskd calls when idle
        assert "IS_SHUTTING_DOWN" in loop
        assert "baseline, never replay history" in loop  # boot must not spam badges
        assert "prev >= 0 and unread > prev" in loop     # toast only on a RISING count
        assert "socketio.start_background_task(_emit_chat_push_loop)" in ws

    def test_core_routes_events_into_the_chat_module(self):
        core = (_ROOT / "webui" / "static" / "core.js").read_text(encoding="utf-8", errors="replace")
        assert "socket.on('chat:room_message'" in core
        assert "socket.on('chat:unread'" in core
        assert "ChatPage.onRoomMessages" in core and "ChatPage.onUnread" in core

    def test_badges_update_on_both_sidebars(self):
        assert "'chat-nav-badge', 'video-chat-nav-badge'" in _CHAT_JS
        # opening the room clears its share of the badge
        assert "unread.room = 0; updateBadges();" in _CHAT_JS
        # a rising PM count toasts (journals into the bell); reads stay silent
        assert "d.grew && typeof showToast === 'function'" in _CHAT_JS


class TestMessageUserHooks:
    """P4 — message-this-user from download/search surfaces."""

    def test_delegated_capture_handler(self):
        assert "data-chat-msg-user" in _CHAT_JS
        assert "}, true);" in _CHAT_JS          # capture phase beats card handlers
        assert "messageUser" in _CHAT_JS

    def test_download_surfaces_render_the_hook(self):
        dl = (_ROOT / "webui" / "static" / "downloads.js").read_text(
            encoding="utf-8", errors="replace")
        assert dl.count("data-chat-msg-user=") == 3      # album + track + candidates
        # escapeHtml leaves double quotes — every attr site must strip them
        assert dl.count("escapeHtml(result.username || '').replace(/\"/g, '&quot;')") == 2
        # candidates: only SOULSEEK peers are messageable (torrent rows aren't users)
        assert "/soulseek/i.test(String(c.source))" in dl
