"""Download orchestrator helpers package.

Lifted from web_server.py download/sync orchestration code. Each module
covers a discrete piece of the pipeline:

- history     — sync_history table writes (start + completion)
- (more arriving in subsequent PRs as the orchestrator gets carved up)
"""
