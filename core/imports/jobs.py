"""Bounded in-process manual import jobs, owned by the submitting profile.

Synchronous API callers remain supported. The browser opts into jobs so a
slow metadata provider never occupies its HTTP request for the whole import.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import time
import uuid

from core.profile_context import set_background_profile, reset_background_profile


class ImportJobs:
    def __init__(self, workers=2, capacity=16):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ManualImport")
        self.capacity = capacity
        self.lock = threading.Lock()
        self.jobs = {}
        self.keys = {}

    def submit(self, owner, key, kind, data, operation):
        fingerprint = hashlib.sha256(json.dumps([kind, data], sort_keys=True).encode()).hexdigest()
        with self.lock:
            now = time.monotonic()
            for job_id, job in list(self.jobs.items()):
                if job['finished'] is not None and now - job['finished'] > 3600:
                    self.jobs.pop(job_id)
                    self.keys.pop((job['owner'], job['key']), None)
            existing = self.keys.get((owner, key))
            if existing:
                job = self.jobs[existing]
                if job['fingerprint'] != fingerprint:
                    return {'success': False, 'error': 'Idempotency key already used for a different import'}, 409
                return {'success': True, 'job_id': existing}, 202
            if sum(j['finished'] is None for j in self.jobs.values()) >= self.capacity or len(self.jobs) >= 1024:
                return {'success': False, 'error': 'Import queue is full; try again later'}, 429
            job_id = uuid.uuid4().hex
            job = dict(owner=owner, key=key, fingerprint=fingerprint,
                       state='queued', finished=None, result=None, status=None)
            self.jobs[job_id] = job
            self.keys[(owner, key)] = job_id

        def run():
            token = set_background_profile(owner)
            try:
                with self.lock:
                    job['state'] = 'running'
                result, status = operation()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Manual import job failed')
                result, status = {'success': False, 'error': 'Import processing failed; check server logs'}, 500
            finally:
                reset_background_profile(token)
            with self.lock:
                job.update(state='complete', result=result, status=status, finished=time.monotonic())
        try:
            self.pool.submit(run)
        except Exception:
            with self.lock:
                self.jobs.pop(job_id, None)
                self.keys.pop((owner, key), None)
            raise
        return {'success': True, 'job_id': job_id}, 202

    def get(self, owner, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job['owner'] != owner:
                return {'success': False, 'error': 'Import job unavailable; check imported files before resubmitting'}, 404
            return {'success': True, 'job_id': job_id, 'state': job['state'],
                    'result': job['result'], 'status': job['status']}, 200


import_jobs = ImportJobs()
