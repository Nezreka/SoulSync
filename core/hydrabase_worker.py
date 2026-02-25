"""
Hydrabase P2P Mirror Worker

Background worker that intercepts search queries and mirrors them to the
Hydrabase P2P network via WebSocket. Fire-and-forget — responses are received
(required by protocol) but discarded. Only processes items when the Hydrabase
WebSocket is connected; items are silently dropped when not connected.
"""

import json
import logging
import queue
import threading
import time
import uuid

logger = logging.getLogger(__name__)


class HydrabaseWorker:
    def __init__(self, get_ws_and_lock):
        """
        Args:
            get_ws_and_lock: Callable returning (ws, lock) tuple for the
                             Hydrabase WebSocket connection.
        """
        self.get_ws_and_lock = get_ws_and_lock

        # Worker state
        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None

        # Queue with cap
        self.queue = queue.Queue(maxsize=1000)

        # Statistics
        self.stats = {
            'sent': 0,
            'dropped': 0,
            'errors': 0
        }

    def start(self):
        if self.running:
            return
        self.running = True
        self.should_stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Hydrabase P2P mirror worker started")

    def stop(self):
        if not self.running:
            return
        self.should_stop = True
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Hydrabase P2P mirror worker stopped")

    def pause(self):
        if not self.running:
            return
        self.paused = True

    def resume(self):
        if not self.running:
            return
        self.paused = False

    def enqueue(self, query, query_type):
        """Non-blocking enqueue. Drops oldest item if queue is full."""
        if not query or not self.running:
            return
        item = {'query': query, 'type': query_type}
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            # Drop oldest, then add new
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                pass

    def get_stats(self):
        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        return {
            'enabled': True,
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'queue_size': self.queue.qsize(),
            'stats': self.stats.copy()
        }

    def _run(self):
        while not self.should_stop:
            try:
                if self.paused:
                    time.sleep(1)
                    continue

                # Non-blocking dequeue with timeout
                try:
                    item = self.queue.get(timeout=1)
                except queue.Empty:
                    continue

                self._process_item(item)
                time.sleep(0.5)  # Rate limit

            except Exception as e:
                logger.error(f"Error in Hydrabase worker loop: {e}")
                self.stats['errors'] += 1
                time.sleep(2)

    def _process_item(self, item):
        ws, lock = self.get_ws_and_lock()

        if ws is None:
            self.stats['dropped'] += 1
            return

        nonce = uuid.uuid4().hex
        payload = json.dumps({
            'request': {
                'type': item['type'],
                'query': item['query']
            },
            'nonce': nonce
        })

        try:
            with lock:
                if not ws.connected:
                    self.stats['dropped'] += 1
                    return
                ws.settimeout(15)
                ws.send(payload)

                # Loop to drain stats/heartbeat messages until we get our response
                deadline = time.time() + 15
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    ws.settimeout(remaining)
                    raw = ws.recv()
                    data = json.loads(raw)

                    # Got our nonce back — done
                    if isinstance(data, dict) and data.get('nonce') == nonce:
                        break
                    # Got results without nonce — assume it's ours
                    if isinstance(data, dict) and 'nonce' not in data:
                        if 'response' in data or 'results' in data or 'data' in data:
                            break
                    # Bare list — results
                    if isinstance(data, list):
                        break
                    # Stats/heartbeat — drain and recv again

            self.stats['sent'] += 1
        except Exception as e:
            logger.debug(f"Hydrabase send failed: {e}")
            self.stats['dropped'] += 1
