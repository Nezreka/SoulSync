"""Serialize maintenance mutation units, including their precondition checks.

Read-only previews and scan analysis do not hold this lock. A scanner briefly
acquires it when persisting a verified assignment relocation. The application
runs a single maintenance process; nested handlers may safely reuse it.
"""
from functools import wraps
from threading import RLock

mutation_lock = RLock()


def maintenance_mutation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with mutation_lock:
            return function(*args, **kwargs)
    return guarded
