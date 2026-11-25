"""Shared response contract for slskd's single-file enqueue endpoint."""


def acknowledged_transfer_id(response: object, username: str, filename: str) -> str | None:
    """Return an ID only when the response identifies one matching transfer."""
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
