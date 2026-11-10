"""Job operations shared by the customer API and the admin console.

Cancelling and retrying used to live inline in the HTTP layer; the admin console
needs exactly the same semantics, and duplicating them would let the two drift
(one refunding, one not). The rules stay here:

* cancelling is a user action, not a failure -- terminal state ``cancelled``, no
  retry, reserved request returned;
* retrying is only legal from ``failed``, and resets the attempt counter so the
  worker actually picks it up again.
"""
from __future__ import annotations

import sqlite3

from ..domain import jobs as jobstates
from ..infrastructure import repos
from ..infrastructure.db import write_tx


class JobNotFound(Exception):
    pass


class JobConflict(Exception):
    def __init__(self, message: str, state: str | None = None):
        super().__init__(message)
        self.state = state


def cancel(container, job_id: str, *, owner_user_id: str | None = None) -> tuple[bool, sqlite3.Row]:
    """Returns (changed, job row). Raises JobNotFound / JobConflict."""
    with container.db.session() as conn:
        job = repos.get_job(conn, job_id)
        if job is None or (owner_user_id is not None and job["user_id"] != owner_user_id):
            raise JobNotFound(job_id)

        if job["state"] in jobstates.TERMINAL:
            return False, job

        try:
            jobstates.assert_transition(job["state"], jobstates.CANCELLED)
        except jobstates.InvalidTransition as exc:
            raise JobConflict(f"cannot cancel a job in state {job['state']}",
                              state=job["state"]) from exc

        with write_tx(conn):
            # Conditional write: the worker may advance the state while we are
            # validating, and whoever commits last must not win blindly.
            applied = repos.set_job_state_if(
                conn, job_id, job["state"], jobstates.CANCELLED, clock=container.clock
            )
        changed = applied
        job = repos.get_job(conn, job_id)

    if changed:
        # Outside the write transaction: it opens its own session.
        container.quota.refund(job["user_id"], job_id)
    return changed, job


def retry(container, job_id: str) -> tuple[bool, sqlite3.Row]:
    """Puts a failed job back in the queue. Returns (changed, job row)."""
    with container.db.session() as conn:
        job = repos.get_job(conn, job_id)
        if job is None:
            raise JobNotFound(job_id)
        if job["state"] != jobstates.FAILED:
            raise JobConflict("only failed jobs can be retried", state=job["state"])

        with write_tx(conn):
            applied = repos.set_job_state_if(
                conn, job_id, jobstates.FAILED, jobstates.QUEUED, clock=container.clock
            )
            if applied:
                # A job that already burned its attempts would be picked up and
                # immediately declared failed again, so give it a fresh budget.
                conn.execute(
                    "UPDATE jobs SET attempts = 0, last_error = NULL, next_retry_at = NULL "
                    "WHERE id = ?",
                    (job_id,),
                )
        job = repos.get_job(conn, job_id)
    return bool(applied), job
