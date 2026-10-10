"""Finite-state operation session and append-only in-memory journal.

The Phase 02 state machine is a safety model only. Its write request always
terminates in WRITE_BLOCKED and has no call site to a flash or restore backend.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from .authorization import (
    AuthorizationError,
    AuthorizationLedger,
    WriteAuthorization,
)
from .compatibility import CompatibilityValidator
from .types import (
    BackupEvidence,
    CompatibilityDecision,
    CompatibilityResult,
    OperationPlan,
    OperationResult,
    OperationState,
)


class WorkflowTransitionError(RuntimeError):
    """An operation attempted an illegal state transition."""


@dataclass(frozen=True)
class JournalEntry:
    sequence: int
    timestamp_utc: str
    action: str
    from_state: OperationState
    to_state: OperationState
    code: str
    message: str


class OperationJournal:
    """Append-only, thread-safe operation event journal."""

    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []
        self._lock = threading.Lock()

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        with self._lock:
            return tuple(self._entries)

    def append(
        self,
        *,
        action: str,
        from_state: OperationState,
        to_state: OperationState,
        code: str,
        message: str,
    ) -> JournalEntry:
        with self._lock:
            entry = JournalEntry(
                sequence=len(self._entries) + 1,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                action=action,
                from_state=from_state,
                to_state=to_state,
                code=code,
                message=message,
            )
            self._entries.append(entry)
            return entry


_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.DRAFT: frozenset({OperationState.VALIDATED, OperationState.BLOCKED}),
    OperationState.VALIDATED: frozenset({OperationState.BACKUP_VERIFIED, OperationState.BLOCKED}),
    OperationState.BACKUP_VERIFIED: frozenset({OperationState.AUTHORIZED, OperationState.BLOCKED}),
    OperationState.AUTHORIZED: frozenset({OperationState.WRITE_BLOCKED, OperationState.BLOCKED}),
    OperationState.WRITE_BLOCKED: frozenset(),
    OperationState.BLOCKED: frozenset(),
    OperationState.FAILED: frozenset(),
    OperationState.READBACK_VERIFIED: frozenset(),
    OperationState.COMPLETE: frozenset(),
}


class WorkflowSession:
    """Own one immutable plan, state, journal, and approval ledger."""

    def __init__(
        self,
        plan: OperationPlan,
        *,
        validator: CompatibilityValidator | None = None,
        ledger: AuthorizationLedger | None = None,
        journal: OperationJournal | None = None,
    ) -> None:
        self.plan = plan
        self.validator = validator or CompatibilityValidator()
        self.ledger = ledger or AuthorizationLedger(self.validator)
        self.journal = journal or OperationJournal()
        self.state = OperationState.DRAFT
        self.compatibility: CompatibilityResult | None = None
        self.backup_verification: CompatibilityResult | None = None
        self.authorization: WriteAuthorization | None = None
        self._lock = threading.RLock()

    def _transition(self, target: OperationState, *, action: str, code: str, message: str) -> None:
        current = self.state
        if target not in _TRANSITIONS[current]:
            raise WorkflowTransitionError(f"illegal workflow transition {current.value} -> {target.value}")
        self.state = target
        self.journal.append(
            action=action,
            from_state=current,
            to_state=target,
            code=code,
            message=message,
        )

    def validate(self) -> CompatibilityResult:
        with self._lock:
            if self.state is not OperationState.DRAFT:
                raise WorkflowTransitionError("validation is only legal from DRAFT")
            result = self.validator.validate(self.plan)
            self.compatibility = result
            if result.decision is CompatibilityDecision.ALLOWED:
                self._transition(
                    OperationState.VALIDATED,
                    action="validate",
                    code="PLAN_VALIDATED",
                    message="all required compatibility evidence passed",
                )
            else:
                self._transition(
                    OperationState.BLOCKED,
                    action="validate",
                    code=f"PLAN_{result.decision.value.upper()}",
                    message="; ".join(result.reasons),
                )
            return result

    def verify_backup(self, backup: BackupEvidence | None = None) -> CompatibilityResult:
        with self._lock:
            if self.state is not OperationState.VALIDATED:
                raise WorkflowTransitionError("backup verification is only legal from VALIDATED")
            if backup is not None:
                # Replacing the immutable plan binds later approval to this
                # exact backup evidence and its on-disk content fingerprints.
                self.plan = replace(self.plan, backup=backup)
            result = self.validator.validate_backup(self.plan)
            self.backup_verification = result
            if result.decision is CompatibilityDecision.ALLOWED:
                self._transition(
                    OperationState.BACKUP_VERIFIED,
                    action="verify-backup",
                    code="BACKUP_VERIFIED",
                    message="two matching full-chip reads verified for each observed chip",
                )
            else:
                self._transition(
                    OperationState.BLOCKED,
                    action="verify-backup",
                    code=f"BACKUP_{result.decision.value.upper()}",
                    message="; ".join(result.reasons),
                )
            return result

    def authorize(self, *, actor: str, explicit_approval: bool) -> WriteAuthorization:
        with self._lock:
            if self.state is not OperationState.BACKUP_VERIFIED:
                raise WorkflowTransitionError("authorization is only legal after verified backups")
            if self.compatibility is None or self.backup_verification is None:
                raise WorkflowTransitionError("validated plan and backup evidence are required")
            token = self.ledger.issue(
                self.plan,
                actor=actor,
                explicit_approval=explicit_approval,
                compatibility=self.compatibility,
                backup_verification=self.backup_verification,
            )
            self.authorization = token
            self._transition(
                OperationState.AUTHORIZED,
                action="authorize",
                code="APPROVAL_ISSUED",
                message="single-use approval bound to evidence digests",
            )
            return token

    def request_write(
        self,
        token: WriteAuthorization,
        *,
        writer: Callable[[OperationPlan], object] | None = None,
    ) -> OperationResult:
        """Consume approval and explicitly block; `writer` is never invoked.

        The optional writer parameter exists only to prove in tests that a
        future or injected backend cannot be called from the Phase 02 session.
        """
        del writer
        with self._lock:
            if self.state is not OperationState.AUTHORIZED:
                raise WorkflowTransitionError("write request is only legal from AUTHORIZED")
            try:
                self.ledger.consume(token, self.plan)
            except AuthorizationError as exc:
                self._transition(
                    OperationState.BLOCKED,
                    action="write-request",
                    code="APPROVAL_REJECTED",
                    message=str(exc),
                )
                return OperationResult(
                    state=self.state,
                    decision=CompatibilityDecision.BLOCKED,
                    code="APPROVAL_REJECTED",
                    message=str(exc),
                )
            self._transition(
                OperationState.WRITE_BLOCKED,
                action="write-request",
                code="WRITES_DISABLED_PHASE_02",
                message="Phase 02 has no firmware writer integration; no write was attempted",
            )
            return OperationResult(
                state=self.state,
                decision=CompatibilityDecision.BLOCKED,
                code="WRITES_DISABLED_PHASE_02",
                message="Phase 02 has no firmware writer integration; no write was attempted",
            )
