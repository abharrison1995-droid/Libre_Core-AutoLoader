"""Single-use, evidence-bound approvals for the future hardware-write phase.

Phase 02 deliberately has no writer integration. This module issues and checks
approvals so later phases can test the safety contract without enabling writes.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .compatibility import CompatibilityValidator
from .types import (
    CompatibilityDecision,
    CompatibilityResult,
    OperationPlan,
    PlanDigests,
    normalized_path,
)


class AuthorizationError(RuntimeError):
    """An approval was missing, stale, invalid, or already consumed."""


class AuthorizationStaleError(AuthorizationError):
    """Bound evidence changed after approval was issued."""


class AuthorizationReusedError(AuthorizationError):
    """A single-use approval was presented more than once."""


@dataclass(frozen=True)
class WriteAuthorization:
    token_id: str
    actor: str
    issued_at_utc: str
    digests: PlanDigests


def _canonical(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _canonical(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Path):
        return normalized_path(str(value))
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, str):
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_plan_digests(plan: OperationPlan) -> PlanDigests:
    """Compute structural digests and actual on-disk content fingerprints."""
    artifact_path = Path(plan.artifact.path)
    if not artifact_path.is_file():
        raise AuthorizationError("firmware artifact disappeared before authorization")
    try:
        artifact_content_hash = _file_sha256(artifact_path)
    except OSError as exc:
        raise AuthorizationError("firmware artifact could not be fingerprinted") from exc
    artifact_sha = _sha256_json({"evidence": plan.artifact, "content_sha256": artifact_content_hash})

    backup_content: list[dict[str, str]] = []
    for pair in plan.backup.pairs:
        for label, raw_path in (("first", pair.first_path), ("second", pair.second_path)):
            path = Path(raw_path)
            if not path.is_file():
                raise AuthorizationError(f"backup file disappeared before authorization: {pair.chip_id}/{label}")
            try:
                content_hash = _file_sha256(path)
            except OSError as exc:
                raise AuthorizationError(f"backup file could not be fingerprinted: {pair.chip_id}/{label}") from exc
            backup_content.append({
                "chip_id": pair.chip_id,
                "read": label,
                "path": normalized_path(str(path)),
                "content_sha256": content_hash,
            })
    backup_sha = _sha256_json({"evidence": plan.backup, "content": backup_content})

    device_sha = _sha256_json(plan.device)
    topology_sha = _sha256_json(plan.topology)
    plan_sha = _sha256_json({
        "plan": plan,
        "artifact_content_sha256": artifact_content_hash,
        "backup_content": backup_content,
    })
    return PlanDigests(
        plan_sha256=plan_sha,
        device_sha256=device_sha,
        artifact_sha256=artifact_sha,
        topology_sha256=topology_sha,
        backup_sha256=backup_sha,
    )


class AuthorizationLedger:
    """Thread-safe in-memory ledger for short-lived, single-use approvals."""

    def __init__(self, validator: CompatibilityValidator | None = None) -> None:
        self._validator = validator or CompatibilityValidator()
        self._entries: dict[str, tuple[WriteAuthorization, bool]] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        plan: OperationPlan,
        *,
        actor: str,
        explicit_approval: bool,
        compatibility: CompatibilityResult,
        backup_verification: CompatibilityResult,
    ) -> WriteAuthorization:
        if not explicit_approval:
            raise AuthorizationError("explicit human approval is required")
        if not actor.strip():
            raise AuthorizationError("approval actor must be recorded")
        if compatibility.decision is not CompatibilityDecision.ALLOWED:
            raise AuthorizationError("compatibility evidence is not fully allowed")
        if backup_verification.decision is not CompatibilityDecision.ALLOWED:
            raise AuthorizationError("complete verified backup evidence is required")
        # Revalidate at the authorization boundary instead of trusting callers.
        if self._validator.validate(plan).decision is not CompatibilityDecision.ALLOWED:
            raise AuthorizationError("plan evidence no longer passes compatibility validation")
        if self._validator.validate_backup(plan).decision is not CompatibilityDecision.ALLOWED:
            raise AuthorizationError("backup evidence no longer passes verification")
        token = WriteAuthorization(
            token_id=secrets.token_urlsafe(32),
            actor=actor.strip(),
            issued_at_utc=datetime.now(timezone.utc).isoformat(),
            digests=compute_plan_digests(plan),
        )
        with self._lock:
            self._entries[token.token_id] = (token, False)
        return token

    def consume(self, token: WriteAuthorization, plan: OperationPlan) -> PlanDigests:
        with self._lock:
            entry = self._entries.get(token.token_id)
            if entry is None or entry[0] != token:
                raise AuthorizationError("approval token is unknown or invalid")
            stored_token, used = entry
            if used:
                raise AuthorizationReusedError("approval token has already been consumed")
            current = compute_plan_digests(plan)
            if current != stored_token.digests:
                self._entries[token.token_id] = (stored_token, True)
                raise AuthorizationStaleError("device, plan, artifact, topology, or backup changed after approval")
            # Consume even though Phase 02 blocks the actual write. A token cannot
            # be held open and reused after a future write backend is added.
            self._entries[token.token_id] = (stored_token, True)
            return current
