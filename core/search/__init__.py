"""Search API helpers package.

Lifted from web_server.py /api/search and /api/enhanced-search/* routes.
Each module exposes pure-ish functions that take dependencies (database,
clients, config_manager, matching_engine) as arguments. Route handlers in
web_server.py stay thin — they parse requests, call into these helpers,
and return jsonify / streaming responses.
"""
