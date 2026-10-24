"""Serialize maintenance mutation units, including their precondition checks.

Read-only previews and scan analysis do not hold this lock. A scanner briefly
acquires it when persisting a verified assignment relocation. The application
runs a single maintenance process; nested handlers may safely reuse it.
"""
from functools import wraps
from contextlib import contextmanager
from threading import Condition, RLock, get_ident


class MaintenanceLock:
    """Exclusive file mutations, with concurrent planning for other albums.

    Unscoped legacy maintenance still excludes all planners. A named album
    operation permits a short plan transaction on a different album; the next
    mutation waits for that transaction, so it cannot overtake its checks.
    """
    def __init__(self):
        self.condition = Condition(RLock())
        self.owner = None
        self.depth = 0
        self.album_id = None
        self.planners = {}

    def acquire(self, blocking=True, timeout=-1, *, album_id=None):
        thread = get_ident()
        with self.condition:
            def available():
                return self.owner == thread or (self.owner is None and
                    not any(owner != thread and count for owner, count in self.planners.items()))
            if not available():
                if not blocking or not self.condition.wait_for(available, None if timeout < 0 else timeout):
                    return False
            if self.owner is None:
                self.owner, self.album_id = thread, str(album_id) if album_id is not None else None
            self.depth += 1
            return True

    def release(self):
        with self.condition:
            if self.owner != get_ident():
                raise RuntimeError('Maintenance lock is not owned by this thread')
            self.depth -= 1
            if not self.depth:
                self.owner, self.album_id = None, None
                self.condition.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_args):
        self.release()

    @contextmanager
    def for_album(self, album_id):
        self.acquire(album_id=album_id)
        try:
            yield
        finally:
            self.release()

    @contextmanager
    def planning(self, album_id):
        thread = get_ident()
        with self.condition:
            self.condition.wait_for(lambda: self.owner in (None, thread) or
                (album_id is not None and self.album_id is not None and str(album_id) != self.album_id))
            self.planners[thread] = self.planners.get(thread, 0) + 1
        try:
            yield
        finally:
            with self.condition:
                self.planners[thread] -= 1
                if not self.planners[thread]:
                    del self.planners[thread]
                self.condition.notify_all()


mutation_lock = MaintenanceLock()


def maintenance_mutation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with mutation_lock:
            return function(*args, **kwargs)
    return guarded


def album_maintenance_mutation(function):
    @wraps(function)
    def guarded(item, *args, **kwargs):
        with mutation_lock.for_album(item.album_id):
            return function(item, *args, **kwargs)
    return guarded
