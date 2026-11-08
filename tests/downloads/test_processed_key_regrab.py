"""a regrab of a file we already processed must be processed again (#1983)."""


def test_a_second_grab_of_an_already_processed_file_is_picked_up_again(monkeypatch):
    """#1983: a quick download of a deezer track the wishlist had already
    grabbed once got the same key, the poll saw it as processed and the file
    sat in downloads until a restart"""
    import web_server
    from core.runtime_state import matched_downloads_context, processed_download_ids

    monkeypatch.setattr(web_server, 'get_current_profile_id', lambda: 1)
    ctx_key = "deezer_dl::3399694||Bing Crosby - Frosty The Snowman"
    processed_download_ids.add(ctx_key)
    try:
        web_server._register_matched_download_context(ctx_key, {'search_result': {}})
        assert ctx_key not in processed_download_ids
    finally:
        matched_downloads_context.pop(ctx_key, None)
        processed_download_ids.discard(ctx_key)

