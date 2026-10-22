"""mcp server: lets an ai client (claude, etc) drive soulsync.

almost every tool is one /api/v1 endpoint. a call is replayed through the
app with the caller's own api key, so the key's scope, the gates and the
rate limit work exactly like the rest api. a read key only ever sees tools
whose endpoint a read key could call anyway.
"""
