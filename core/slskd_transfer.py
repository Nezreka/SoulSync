"""Shared response contract for slskd's single-file enqueue endpoint."""


def acknowledged_transfer_id(response: object, username: str, filename: str) -> str | None:
    """Return an ID only when the response identifies one matching transfer.

    slskd 0.23 and older answer a successful enqueue with a bare 201 and no
    body, which the client reads as ``{}`` (a failed request is None, never
    {}). that is an accepted enqueue with no id, so the filename stands in
    for one, as it always did there (``_looks_like_transfer_id`` knows a
    filename id). without this every soulseek download failed on those
    versions."""
    if isinstance(response, dict) and not response:
        return filename or None
    if isinstance(response, dict) and ('enqueued' in response or 'failed' in response):
        # This POST submitted only one file, so a failure cannot coexist
        # with a confirmed transfer. Do not depend on failure tuple fields.
        if response.get('failed', []) != []:
            return None
        transfers = response.get('enqueued')
        # A current response carries both fields on each Transfer record.
        default_filename = default_username = None
    else:
        # Preserve explicit IDs in the previously recognized flat object
        # and bare list responses, but never fall back to the filename.
        transfers = [response] if isinstance(response, dict) else response
        default_filename, default_username = filename, username

    if not isinstance(transfers, list):
        return None
    matches = [
        transfer for transfer in transfers if isinstance(transfer, dict)
        and transfer.get('filename', default_filename) == filename
        and transfer.get('username', default_username) == username
    ]
    transfer_id = matches[0].get('id') if len(matches) == 1 else None
    return transfer_id if isinstance(transfer_id, str) and transfer_id.strip() else None
