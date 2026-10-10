"""Explicit-path persistence for unauthenticated raw device evidence candidates."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, cast

from .profiles import V1_PROFILES
from .types import DeviceEvidence


class CandidateEvidenceError(ValueError):
    """Candidate evidence is malformed, unsafe, or has a bad checksum."""


_STRING_FIELDS = frozenset({
    "raw_dmi_product_name",
    "raw_dmi_product_version",
    "raw_dmi_system_family",
    "raw_mtm",
    "raw_bios_version",
    "raw_bios_vendor",
    "manual_model_candidate",
})
_OPTIONAL_BOOL_FIELDS = frozenset({
    "stock_lenovo_bios",
    "internal_flash_unlock_status",
})
_BOOL_FIELDS = frozenset({"internal_flash_unlock_observed"})
_DEVICE_FIELDS = _STRING_FIELDS | _OPTIONAL_BOOL_FIELDS | _BOOL_FIELDS


def device_evidence_from_mapping(value: Mapping[str, object]) -> DeviceEvidence:
    """Validate untrusted JSON values before constructing domain evidence."""
    if set(value) - _DEVICE_FIELDS:
        raise CandidateEvidenceError("candidate evidence contains unsupported fields")
    for key, item in value.items():
        if key in _STRING_FIELDS:
            if item is not None and not isinstance(item, str):
                raise CandidateEvidenceError(f"candidate evidence field {key} must be a string or null")
        elif key in _OPTIONAL_BOOL_FIELDS:
            if item is not None and type(item) is not bool:
                raise CandidateEvidenceError(f"candidate evidence field {key} must be a boolean or null")
        elif key in _BOOL_FIELDS and type(item) is not bool:
            raise CandidateEvidenceError(f"candidate evidence field {key} must be a boolean")
    try:
        return DeviceEvidence(**cast(Any, dict(value)))
    except TypeError as exc:
        raise CandidateEvidenceError("candidate raw device evidence fields are invalid") from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _path_has_link_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    components = absolute.parts[1:] if absolute.anchor else absolute.parts
    for component in components:
        current = current / component
        if current.is_symlink():
            return True
        is_junction = getattr(current, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
    return False


class CandidateEvidenceStore:
    """Store one explicit candidate document without authenticating or approving it.

    The checksum detects accidental corruption only. It is not a signature and
    does not establish that the values came from hardware.
    """

    schema_version = 1

    @classmethod
    def save(
        cls,
        path: str | Path,
        *,
        profile_id: str,
        evidence: DeviceEvidence,
        evidence_ref: str,
    ) -> Path:
        if profile_id not in V1_PROFILES:
            raise CandidateEvidenceError("candidate evidence profile is not in the V1 allowlist")
        if not evidence_ref.strip():
            raise CandidateEvidenceError("candidate evidence reference is required")
        target = Path(path).expanduser().absolute()
        if _path_has_link_component(target):
            raise CandidateEvidenceError("candidate evidence store path contains a symlink or junction")
        if not target.parent.is_dir():
            raise CandidateEvidenceError("candidate evidence store parent directory must already exist")
        if target.exists():
            raise CandidateEvidenceError("candidate evidence store already exists; refusing to overwrite it")
        evidence = device_evidence_from_mapping(asdict(evidence))
        payload: dict[str, object] = {
            "profile_id": profile_id,
            "evidence_ref": evidence_ref.strip(),
            "candidate_only": True,
            "evidence": asdict(evidence),
        }
        envelope = {
            "schema_version": cls.schema_version,
            "payload": payload,
            "payload_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
        }
        encoded = _canonical(envelope)
        try:
            with target.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise CandidateEvidenceError("candidate evidence store already exists; refusing to overwrite it") from exc
        except OSError as exc:
            raise CandidateEvidenceError(f"could not persist candidate evidence: {exc}") from exc
        return target

    @classmethod
    def load(cls, path: str | Path) -> tuple[str, DeviceEvidence, str]:
        target = Path(path).expanduser().absolute()
        if _path_has_link_component(target) or not target.is_file():
            raise CandidateEvidenceError("candidate evidence store is missing or unsafe")
        try:
            envelope = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateEvidenceError("candidate evidence store is unreadable or malformed") from exc
        if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "payload", "payload_sha256"}:
            raise CandidateEvidenceError("candidate evidence envelope has an invalid shape")
        payload = envelope.get("payload")
        if envelope.get("schema_version") != cls.schema_version or not isinstance(payload, dict):
            raise CandidateEvidenceError("candidate evidence store version or payload is unsupported")
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        if envelope.get("payload_sha256") != digest:
            raise CandidateEvidenceError("candidate evidence checksum does not match")
        if set(payload) != {"profile_id", "evidence_ref", "candidate_only", "evidence"}:
            raise CandidateEvidenceError("candidate evidence payload has an invalid shape")
        profile_id = payload.get("profile_id")
        evidence_ref = payload.get("evidence_ref")
        evidence_value = payload.get("evidence")
        if (
            not isinstance(profile_id, str)
            or profile_id not in V1_PROFILES
            or not isinstance(evidence_ref, str)
            or not evidence_ref.strip()
            or payload.get("candidate_only") is not True
            or not isinstance(evidence_value, dict)
        ):
            raise CandidateEvidenceError("candidate evidence profile, reference, or state is invalid")
        evidence = device_evidence_from_mapping(evidence_value)
        return str(profile_id), evidence, evidence_ref
