"""Serve upgrade progress while the protected application boot runs in a worker.

The database still takes and verifies its immutable pre-upgrade snapshot before
any schema change. Moving the entire protected boot off the WSGI import thread
lets Gunicorn accept HTTP immediately without permitting requests or workers
into a half-initialized catalogue.
"""
import json
import logging
import threading

logger = logging.getLogger(__name__)

_PAGE = b'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SoulSync is starting</title><style>
body{margin:0;background:#101117;color:#edeef3;font:17px/1.6 system-ui,sans-serif;
min-height:100vh;display:grid;place-items:center}main{max-width:36rem;padding:2rem}
h1{font-size:1.65rem}p{color:#b4b8c7}small{color:#9299ad}
</style><main><small>SoulSync</small><h1>Preparing your library</h1>
<p id="status">Your library is being backed up and prepared. This can take a while
for a large collection. This page will open automatically when it is ready.</p>
<small>You can follow the progress in the server log.</small></main>
<script>async function poll(){try{const r=await fetch(__STARTUP_STATUS_URL__,
{cache:'no-store'});const s=await r.json();if(s.status==='ready'){
location.reload();return}if(s.status==='failed'){document.getElementById('status').textContent=
'Startup could not finish. Check the server log, resolve the reported problem and restart SoulSync.';
return}}catch(e){}setTimeout(poll,2000)}poll()</script></html>'''


class StartupApplication:
    def __init__(self, loader):
        self._app = None
        self._failed = False
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._load, args=(loader,),
                                        name='SoulSyncStartup', daemon=True)
        self._thread.start()

    def _load(self, loader):
        try:
            self._app = loader()
        except Exception:
            self._failed = True
            logger.exception('SoulSync startup failed; application remains unavailable')
        finally:
            self._done.set()

    def wait(self, timeout=None):
        return self._done.wait(timeout)

    def __call__(self, environ, start_response):
        if environ.get('PATH_INFO') == '/api/startup-status':
            status = 'failed' if self._failed else ('ready' if self._app else 'starting')
            body = json.dumps({'status': status}).encode()
            start_response('503 Service Unavailable' if self._failed else '200 OK',
                           [('Content-Type', 'application/json'), ('Cache-Control', 'no-store')])
            return [body]
        if self._app is not None:
            return self._app(environ, start_response)
        if environ.get('PATH_INFO', '/').startswith('/api/'):
            start_response('503 Service Unavailable', [('Content-Type', 'application/json'),
                                                      ('Retry-After', '2')])
            return [b'{"success":false,"error":"SoulSync is starting"}']
        start_response('503 Service Unavailable' if self._failed else '200 OK',
                       [('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store')])
        # SCRIPT_NAME is the mount point supplied by the WSGI reverse proxy.
        # JSON escaping plus HTML escaping keeps it safe inside the script.
        endpoint = str(environ.get('SCRIPT_NAME') or '').rstrip('/') + '/api/startup-status'
        encoded = json.dumps(endpoint).replace('<', '\\u003c').encode()
        return [_PAGE.replace(b'__STARTUP_STATUS_URL__', encoded)]
