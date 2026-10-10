"""Recovery-readiness evidence; this module never performs restoration writes."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.types import CompatibilityDecision, OperationPlan

from .backup_service import BackupManifestError, BackupManifestStore, BackupSetResult


@dataclass(frozen=True)
class RecoveryReadinessRecord:
    record_id: str
    operation_id: str
    profile_id: str
    status: str
    created_at_utc: str
    primary_backup_sha256: str
    recovery_backup_sha256: str
    primary_manifest_sha256: str
    recovery_manifest_sha256: str
    primary_volume_id: str
    recovery_volume_id: str
    runbook: str
    recovery_method: str
    recovery_write_enabled: bool
    hardware_recovery_tested: bool
    reasons: tuple[str, ...]


class RecoveryReadinessService:
    """Verify that a model has two durable original copies and a runbook."""

    _METHODS = {
        "t430-1vyrain": "external SPI recovery using the separately identified physical chip and its matching original backup",
        "x200-libreboot": "external SPI recovery using the observed regular-X200 chip and its matching original backup",
        "x220-coreboot": "external SPI recovery with the observed IFD/GBE/ME/BIOS layout preserved and checked",
    }

    def __init__(self, validator: CompatibilityValidator | None = None) -> None:
        self.validator = validator or CompatibilityValidator()

    def evaluate(self, plan: OperationPlan, backup: BackupSetResult) -> RecoveryReadinessRecord:
        reasons: list[str] = []
        plan_decision = self.validator.validate(plan)
        if plan_decision.decision is not CompatibilityDecision.ALLOWED:
            reasons.append("operation plan is not currently validated: " + "; ".join(plan_decision.reasons))
        if backup.profile_id != plan.profile_id:
            reasons.append("backup set profile does not match the operation plan")
        if backup.backup != plan.backup and plan.backup.pairs:
            reasons.append("plan backup evidence differs from the captured backup set")
        if not backup.primary_storage.usable or not backup.recovery_storage.usable:
            reasons.append("both recorded backup destinations must remain persistent and readable")
        if backup.primary_storage.volume_id == backup.recovery_storage.volume_id:
            reasons.append("primary and recovery copies are not on separate volumes")
        expected = self.validator.validate_backup(replace(plan, backup=backup.backup))
        if expected.decision is not CompatibilityDecision.ALLOWED:
            reasons.extend(expected.reasons)
        if not backup.chips or {item.chip_id for item in backup.chips} != {
            item.chip_id for item in plan.topology.chips
        }:
            reasons.append("backup set does not cover exactly the plan's observed chips")
        for item in backup.chips:
            for label, path_value, declared_hash in (
                ("primary", item.first_path, item.first_sha256),
                ("recovery", item.second_path, item.second_sha256),
            ):
                path = Path(path_value)
                try:
                    if not path.is_file() or path.stat().st_size != item.capacity_bytes:
                        reasons.append(f"{label} copy for {item.chip_id} is missing or has the wrong size")
                    elif _hash_file(path) != declared_hash:
                        reasons.append(f"{label} copy for {item.chip_id} no longer matches its recorded SHA-256")
                except OSError:
                    reasons.append(f"{label} copy for {item.chip_id} could not be reread")

        primary_manifest = BackupManifestStore(Path(backup.primary_manifest).parent)
        recovery_manifest = BackupManifestStore(Path(backup.recovery_manifest).parent)
        primary_event = _verified_event(primary_manifest, backup.operation_id, reasons, "primary")
        recovery_event = _verified_event(recovery_manifest, backup.operation_id, reasons, "recovery")
        if primary_event is not None and recovery_event is not None:
            if _event_content(primary_event) != _event_content(recovery_event):
                reasons.append("primary and recovery manifests disagree about the verified backup set")
        for label, event in (("primary", primary_event), ("recovery", recovery_event)):
            if event is not None:
                _validate_manifest_plan(event, plan, reasons, label)
        runbook_rel = "docs/v1/RECOVERY_RUNBOOK.md"
        repo_root = Path(__file__).resolve().parents[2]
        if not (repo_root / runbook_rel).is_file():
            reasons.append("model-specific recovery runbook is missing from the installation")
        method = self._METHODS.get(plan.profile_id)
        if method is None:
            reasons.append("profile has no reviewed recovery method")
            method = "unavailable"
        return RecoveryReadinessRecord(
            record_id=str(uuid.uuid4()),
            operation_id=backup.operation_id,
            profile_id=plan.profile_id,
            status="ready" if not reasons else "blocked",
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            primary_backup_sha256=_aggregate_hash((item.chip_id, item.first_sha256) for item in backup.chips),
            recovery_backup_sha256=_aggregate_hash((item.chip_id, item.second_sha256) for item in backup.chips),
            primary_manifest_sha256=backup.primary_manifest_sha256,
            recovery_manifest_sha256=backup.recovery_manifest_sha256,
            primary_volume_id=backup.primary_storage.volume_id,
            recovery_volume_id=backup.recovery_storage.volume_id,
            runbook=runbook_rel,
            recovery_method=method,
            recovery_write_enabled=False,
            hardware_recovery_tested=False,
            reasons=tuple(reasons),
        )


def _verified_event(
    store: BackupManifestStore,
    operation_id: str,
    reasons: list[str],
    label: str,
) -> dict[str, Any] | None:
    try:
        events = store.read_events()
    except BackupManifestError as exc:
        reasons.append(f"{label} backup manifest is invalid: {exc}")
        return None
    matching = [event for event in events if event.get("operation_id") == operation_id]
    if not matching or matching[-1].get("status") != "verified":
        reasons.append(f"{label} manifest does not end with a verified event for this backup operation")
        return None
    return matching[-1]


def _validate_manifest_plan(
    event: dict[str, Any],
    plan: OperationPlan,
    reasons: list[str],
    label: str,
) -> None:
    if event.get("profile_id") != plan.profile_id or event.get("requested_model_id") != plan.requested_model_id:
        reasons.append(f"{label} manifest profile or model differs from the current plan")
    expected_device = {
        "raw_dmi_product_name": plan.device.raw_dmi_product_name,
        "raw_mtm": plan.device.raw_mtm,
        "raw_bios_version": plan.device.raw_bios_version,
    }
    if event.get("device_identifiers") != expected_device:
        reasons.append(f"{label} manifest device identifiers differ from the current plan")
    expected_artifact = {
        "sha256": plan.artifact.expected_sha256,
        "size_bytes": plan.artifact.expected_size_bytes,
        "source_revision": plan.artifact.source_revision,
    }
    if event.get("source_artifact") != expected_artifact:
        reasons.append(f"{label} manifest artifact provenance differs from the current plan")

    expected_chips = {
        chip.chip_id: (chip.physical_label, chip.capacity_bytes, chip.voltage_mv)
        for chip in plan.topology.chips
    }
    raw_chips = event.get("topology")
    actual_chips: dict[str, tuple[Any, Any, Any]] = {}
    if isinstance(raw_chips, list):
        for chip in raw_chips:
            if not isinstance(chip, dict) or not isinstance(chip.get("chip_id"), str):
                continue
            chip_id = chip["chip_id"]
            if chip_id in actual_chips:
                continue
            actual_chips[chip_id] = (
                chip.get("physical_label"),
                chip.get("capacity_bytes"),
                chip.get("voltage_mv"),
            )
    if actual_chips != expected_chips:
        reasons.append(f"{label} manifest chip topology differs from the current plan")

    expected_regions = tuple(sorted(
        (region.name, region.start_byte, region.end_byte)
        for region in plan.topology.regions
    ))
    for field in ("regions", "region_map"):
        raw_regions = event.get(field)
        if not isinstance(raw_regions, list):
            reasons.append(f"{label} manifest {field} is missing or malformed")
            continue
        try:
            actual_regions = tuple(sorted(
                (region["name"], region["start_byte"], region["end_byte"])
                for region in raw_regions
                if isinstance(region, dict)
            ))
        except (KeyError, TypeError):
            reasons.append(f"{label} manifest {field} is malformed")
            continue
        if len(actual_regions) != len(raw_regions) or actual_regions != expected_regions:
            reasons.append(f"{label} manifest {field} differs from the current plan")


def _event_content(event: dict[str, Any]) -> bytes:
    excluded = {"sequence", "previous_sha256", "event_sha256"}
    content = {key: value for key, value in event.items() if key not in excluded}
    return json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aggregate_hash(entries: Any) -> str:
    payload = json.dumps(sorted(entries), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
