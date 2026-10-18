"""Serialize maintenance mutation units, including their precondition checks.

Scanners and read-only previews do not hold this lock. The application runs a
single maintenance process; nested legacy handlers may safely reuse it.
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
