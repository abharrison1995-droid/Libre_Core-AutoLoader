"""Explicit, local adapter for the shared X200/X220 workflow controller.

Construction is side-effect free. Hardware queries, chip reads, backup reads,
and firmware compilation happen only when an operator submits a typed action.
The adapter never accepts raw identity, chip names, capacity, or ``observed``
flags from request JSON. Those values are reconstructed from fresh local DMI
and flashrom probe evidence.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from autoloader.detection.bios import BIOSDetector
from autoloader.detection.model import ModelDetector
from autoloader.flasher.backup_service import BackupSetResult, BackupSetService
from autoloader.flasher.recovery import RecoveryReadinessService
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.flasher.spi import (
    ChipProbeEvidence,
    FlashromReadOnlyTransport,
    ReadOnlySPITransport,
    SPIProbeService,
)
from autoloader.rom_builder.artifact_engine import (
    ArtifactBuildResult,
    ArtifactEngine,
    BlobInput,
    BuildRequest,
    BuildStatus,
    X220_COREBOOT,
    parse_ifdtool_regions,
    sha256_file,
    validate_x220_backup_evidence,
)
from autoloader.reversion.t430_handoff import T430DetectionCapture
from autoloader.workflow.compatibility import CompatibilityValidator
from autoloader.workflow.profiles import MIB
from autoloader.workflow.types import (
    BuildIntentEvidence,
    ChipEvidence,
    ChipTopology,
    ProgrammerEvidence,
    ProgrammerKind,
    build_intent_sha256,
)
from autoloader.workflow.x200_libreboot import create_x200_build_intent
from autoloader.workflow.x220_coreboot import create_x220_build_intent


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHECKPOINT_SCHEMA = "librecore.workflow-session"
_CHECKPOINT_VERSION = 1


class LocalWorkflowEvidenceAdapter:
    """Default application adapter backed by local read-only tools/services.

    This class intentionally performs no work in ``__init__``. Each method is
    an explicit operator action. X220 preparation performs one preliminary
    full-chip read to obtain the descriptor map with the exact pinned ifdtool;
    the later backup action performs the required two independent reads.
    """

    def __init__(
        self,
        *,
        model_detector: ModelDetector | None = None,
        bios_detector: BIOSDetector | None = None,
        transport: ReadOnlySPITransport | None = None,
        backup_service: BackupSetService | None = None,
        artifact_engine: ArtifactEngine | None = None,
        validator: CompatibilityValidator | None = None,
    ) -> None:
        self.model_detector = model_detector or ModelDetector()
        self.bios_detector = bios_detector or BIOSDetector()
        self.transport = transport or FlashromReadOnlyTransport()
        self.backup_service = backup_service or BackupSetService(self.transport)
        self.artifact_engine = artifact_engine or ArtifactEngine()
        self.validator = validator or CompatibilityValidator()
        self._last_x220_region_tool: dict[str, str] | None = None

    def prepare_build_intent(
        self, profile_id: str, checkpoint_path: str | Path, request_document: str
    ) -> BuildIntentEvidence:
        target = Path(checkpoint_path).expanduser()
        if target.exists():
            raise ValueError("Workflow preparation requires a new checkpoint path; existing state is never overwritten")
        request = self._parse_request(request_document)
        capture, probe, intent = self._observe(profile_id, request)
        checkpoint = self._checkpoint_record(profile_id, request, capture, probe, intent)
        self._write_checkpoint(target, checkpoint)
        return intent

    def load_build_intent(
        self,
        profile_id: str,
        checkpoint_path: Path | None = None,
        request_document: str | None = None,
    ) -> BuildIntentEvidence:
        if checkpoint_path is None or request_document is None:
            raise ValueError("X200/X220 preparation requires its versioned local-evidence checkpoint and fresh attestation")
        saved = self._read_checkpoint(checkpoint_path, profile_id)
        request = self._parse_request(request_document)
        capture, probe, intent = self._observe(profile_id, request)
        if saved.get("intent_sha256") != build_intent_sha256(intent):
            raise ValueError("Fresh local DMI, probe, or operator-attested topology differs from the saved build intent")
        # Path choices are operator configuration, not observed evidence. They
        # must still match the original checkpoint to prevent silent retargeting.
        for key in ("primary_directory", "recovery_directory", "source_directory", "output_root"):
            if saved.get("operator", {}).get(key) != request[key]:
                raise ValueError(f"Workflow checkpoint {key} differs from the explicit current request")
        if saved.get("probe", {}).get("raw_output_sha256") != probe.raw_output_sha256:
            raise ValueError("Fresh flashrom probe differs from the saved observation")
        saved_probe = saved.get("probe")
        if not isinstance(saved_probe, dict) or any(
            saved_probe.get(key) != value
            for key, value in (
                ("chip_name", probe.chip_name),
                ("capacity_bytes", probe.capacity_bytes),
                ("tool_name", probe.tool.name),
                ("tool_version", probe.tool.version),
                ("tool_executable", probe.tool.executable),
            )
        ):
            raise ValueError("Fresh probe chip or flashrom tool identity differs from the saved observation")
        return intent

    def checkpoint_progress(self, profile_id: str, checkpoint_path: str | Path) -> tuple[bool, bool]:
        """Report durable checkpoint markers; never reconstruct trusted result objects."""
        saved = self._read_checkpoint(checkpoint_path, profile_id)
        backup = saved.get("backup")
        has_backup = isinstance(backup, dict) and all(
            isinstance(backup.get(key), str) and bool(backup.get(key))
            for key in (
                "operation_id", "primary_manifest", "recovery_manifest",
                "primary_manifest_sha256", "recovery_manifest_sha256",
            )
        )
        artifact = saved.get("artifact_manifest")
        has_artifact = isinstance(artifact, str) and bool(artifact)
        return has_backup, has_artifact

    def capture_backup(
        self,
        profile_id: str,
        intent: BuildIntentEvidence,
        checkpoint_path: Path,
        request_document: str,
    ) -> BackupSetResult:
        saved = self._read_checkpoint(checkpoint_path, profile_id)
        request = self._parse_request(request_document)
        capture, probe, current_intent = self._observe(profile_id, request)
        self._require_same_intent(intent, current_intent, saved, probe)
        result = self.backup_service.capture(
            current_intent,
            (probe,),
            programmer=self._programmer(request["programmer"]),
            programmer_evidence=current_intent.programmer,
            primary_directory=request["primary_directory"],
            recovery_directory=request["recovery_directory"],
            primary_persistent_confirmed=request["primary_persistent_confirmed"],
            recovery_persistent_confirmed=request["recovery_persistent_confirmed"],
        )
        saved["backup"] = {
            "operation_id": result.operation_id,
            "primary_manifest": result.primary_manifest,
            "recovery_manifest": result.recovery_manifest,
            "primary_manifest_sha256": result.primary_manifest_sha256,
            "recovery_manifest_sha256": result.recovery_manifest_sha256,
        }
        saved["operator"] = self._operator_record(request)
        self._write_checkpoint(checkpoint_path, saved)
        return result

    def build_artifact(
        self,
        profile_id: str,
        intent: BuildIntentEvidence,
        backup: BackupSetResult,
        checkpoint_path: Path,
        request_document: str,
        *,
        validated_blob_inputs: tuple[BlobInput, ...] = (),
    ) -> ArtifactBuildResult:
        saved = self._read_checkpoint(checkpoint_path, profile_id)
        request = self._parse_request(request_document)
        _, probe, current_intent = self._observe(profile_id, request)
        self._require_same_intent(intent, current_intent, saved, probe)
        if profile_id == "x200-libreboot":
            result = self.artifact_engine.build(BuildRequest(
                recipe_id=intent.recipe_id,
                source_dir=Path(request["source_directory"]),
                output_root=Path(request["output_root"]),
                observed_chip_capacity_bytes=intent.expected_size_bytes,
                build_intent=intent,
                cpu_count=request["cpu_count"],
            ))
        else:
            required_blob_names = {"descriptor.bin", "me.bin", "gbe.bin"}
            if {item.name for item in validated_blob_inputs} != required_blob_names or len(validated_blob_inputs) != len(required_blob_names):
                raise ValueError("X220 builder requires the three exact backup-derived region blobs")
            validate_x220_backup_evidence(
                intent, backup, intent.topology.regions, validated_blob_inputs
            )
            reopened_backup = self.load_verified_backup(
                profile_id, intent, checkpoint_path, request_document
            )
            if reopened_backup != backup:
                raise ValueError("X220 build backup differs from the exact persisted verified backup set")
            result = self.artifact_engine.build(BuildRequest(
                recipe_id=X220_COREBOOT.recipe_id,
                source_dir=Path(request["source_directory"]),
                output_root=Path(request["output_root"]),
                blob_inputs=validated_blob_inputs,
                expected_regions=intent.topology.regions,
                observed_chip_capacity_bytes=intent.expected_size_bytes,
                backup_intent=intent,
                backup_set=backup,
                cpu_count=request["cpu_count"],
            ))
        if result.status is BuildStatus.COMPLETE and result.manifest_path is not None:
            manifest = Path(result.manifest_path).resolve(strict=True)
            output_root = Path(request["output_root"]).resolve(strict=True)
            if not self._is_within(manifest, output_root) or manifest.name != "manifest.json":
                raise ValueError("Completed artifact manifest is outside the explicitly selected output root")
            if self._has_link_component(manifest, output_root):
                raise ValueError("Completed artifact manifest path contains a symlink or junction")
            saved["artifact_manifest"] = str(manifest)
            self._write_checkpoint(checkpoint_path, saved)
        return result

    def load_verified_backup(
        self,
        profile_id: str,
        intent: BuildIntentEvidence,
        checkpoint_path: Path | None = None,
        request_document: str | None = None,
    ) -> BackupSetResult:
        if checkpoint_path is None or request_document is None:
            raise ValueError("Backup resume requires the versioned checkpoint and fresh setup attestation")
        saved = self._read_checkpoint(checkpoint_path, profile_id)
        metadata = saved.get("backup")
        if not isinstance(metadata, dict):
            raise ValueError("No completed backup is recorded in the workflow checkpoint")
        request = self._parse_request(request_document)
        _, probe, current_intent = self._observe(profile_id, request)
        self._require_same_intent(intent, current_intent, saved, probe)
        result = self.backup_service.load_verified_result(
            current_intent,
            operation_id=self._required_saved_text(metadata, "operation_id"),
            primary_manifest=self._required_saved_text(metadata, "primary_manifest"),
            recovery_manifest=self._required_saved_text(metadata, "recovery_manifest"),
            primary_manifest_sha256=self._required_saved_text(metadata, "primary_manifest_sha256"),
            recovery_manifest_sha256=self._required_saved_text(metadata, "recovery_manifest_sha256"),
            expected_tool=probe.tool,
        )
        if result.programmer != request["programmer"]:
            raise ValueError("Persisted backup used a different external programmer than the current explicit selection")
        readiness = RecoveryReadinessService(self.validator).evaluate(current_intent, result)
        if readiness.status != "ready" or readiness.operation_id != result.operation_id:
            raise ValueError("Persisted backup copies/manifests no longer pass recovery readiness: " + "; ".join(readiness.reasons))
        return result

    def load_completed_artifact(
        self,
        profile_id: str,
        intent: BuildIntentEvidence,
        backup: BackupSetResult,
        checkpoint_path: Path | None = None,
        request_document: str | None = None,
    ) -> ArtifactBuildResult:
        if checkpoint_path is None or request_document is None:
            raise ValueError("Artifact resume requires the versioned checkpoint and fresh setup attestation")
        saved = self._read_checkpoint(checkpoint_path, profile_id)
        manifest_path = saved.get("artifact_manifest")
        if not isinstance(manifest_path, str) or not Path(manifest_path).is_absolute():
            raise ValueError("No absolute completed artifact manifest is recorded in the workflow checkpoint")
        request = self._parse_request(request_document)
        manifest = Path(manifest_path)
        output_root = Path(request["output_root"]).resolve(strict=True)
        try:
            resolved_manifest = manifest.resolve(strict=True)
            if not self._is_within(resolved_manifest, output_root) or resolved_manifest.name != "manifest.json":
                raise ValueError("Recorded artifact manifest is outside the explicit output root")
            if self._has_link_component(resolved_manifest, output_root):
                raise ValueError("Recorded artifact manifest path contains a symlink or junction")
        except OSError as exc:
            raise ValueError("Recorded artifact manifest path is unavailable") from exc
        _, probe, current_intent = self._observe(profile_id, request)
        self._require_same_intent(intent, current_intent, saved, probe)
        checked_backup = self.load_verified_backup(profile_id, current_intent, checkpoint_path, request_document)
        if checked_backup.operation_id != backup.operation_id:
            raise ValueError("Freshly reopened backup operation differs from the controller-bound backup")
        result = self.artifact_engine.load_completed_result(resolved_manifest)
        if result.recipe_id != current_intent.recipe_id or result.status is not BuildStatus.COMPLETE:
            raise ValueError("Reopened persisted artifact does not match the current profile recipe")
        return result

    def _observe(
        self, profile_id: str, request: dict[str, Any]
    ) -> tuple[T430DetectionCapture, ChipProbeEvidence, BuildIntentEvidence]:
        capture = T430DetectionCapture.detect_now(
            model_detector=self.model_detector, bios_detector=self.bios_detector
        )
        programmer_type = self._programmer(request["programmer"])
        probe = SPIProbeService(self.transport).probe(programmer_type)
        if probe.capacity_bytes <= 0:
            raise ValueError("SPI probe returned a non-positive capacity")
        chip = ChipEvidence(
            chip_id=probe.chip_name,
            capacity_bytes=probe.capacity_bytes,
            observed=True,
            physical_label="single chip identified by local read-only flashrom probe",
            voltage_mv=request["measured_voltage_mv"],
            voltage_evidence_source="user-reported-manual-measurement",
            voltage_attested_by=request["actor"],
            voltage_attested_at=request["attested_at"],
            voltage_instrument_id=request["instrument_id"],
            voltage_test_point=request["measurement_test_point"],
        )
        programmer = ProgrammerEvidence(
            kind=ProgrammerKind.EXTERNAL_SPI,
            observed=True,
            interface_verified=True,
            wiring_verified=True,
            voltage_mv=request["measured_voltage_mv"],
            evidence_source="user-reported-manual-measurement",
            attested_by=request["actor"],
            attested_at=request["attested_at"],
            instrument_id=request["instrument_id"],
            measurement_test_point=request["measurement_test_point"],
        )
        topology = ChipTopology(observed=True, chips=(chip,), regions=())
        if profile_id == "x220-coreboot":
            ifd_regions = self._capture_x220_regions(probe, request["source_directory"])
            topology = ChipTopology(observed=True, chips=(chip,), regions=ifd_regions)
        if profile_id == "x200-libreboot":
            intent = create_x200_build_intent(device=capture.device, topology=topology, programmer=programmer)
        elif profile_id == "x220-coreboot":
            intent = create_x220_build_intent(device=capture.device, topology=topology, programmer=programmer)
        else:
            raise ValueError("Only the explicit X200 Libreboot and X220 Coreboot profiles are supported")
        compatibility = self.validator.validate_build_intent(intent)
        if compatibility.decision.value != "allowed":
            raise ValueError("Fresh local identity/topology evidence is not allowed: " + "; ".join(compatibility.reasons))
        return capture, probe, intent

    def _capture_x220_regions(self, probe: ChipProbeEvidence, source_directory: str) -> tuple[Any, ...]:
        if probe.capacity_bytes != 8 * MIB:
            raise ValueError("X220 requires one locally probed 8 MiB chip before region inspection")
        source = Path(source_directory).expanduser().resolve(strict=True)
        audit = self.artifact_engine.inspect_source(X220_COREBOOT.recipe_id, source)
        if not audit.clean:
            raise ValueError("Pinned Coreboot source is not clean")
        git = shutil.which("git")
        make = shutil.which("make")
        if git is None or make is None:
            raise ValueError("X220 region preparation requires local Git and make; no network or firmware build was attempted")
        with tempfile.TemporaryDirectory(prefix="librecore-x220-ifdtool-") as temp_dir:
            checkout = Path(temp_dir) / "coreboot"
            clone = subprocess.run(
                [git, "clone", "--shared", "--no-checkout", str(source), str(checkout)],
                capture_output=True, text=True, check=False, timeout=120,
            )
            if clone.returncode != 0:
                raise ValueError("Could not create an isolated local checkout of the already pinned Coreboot source")
            checkout_result = subprocess.run(
                [git, "-C", str(checkout), "checkout", "--detach", X220_COREBOOT.source.revision],
                capture_output=True, text=True, check=False, timeout=120,
            )
            if checkout_result.returncode != 0:
                raise ValueError("The pinned Coreboot revision is unavailable in the local source repository")
            remote = subprocess.run(
                [git, "-C", str(checkout), "remote", "set-url", "origin", X220_COREBOOT.source.repository_url],
                capture_output=True, text=True, check=False, timeout=30,
            )
            if remote.returncode != 0:
                raise ValueError("Could not bind the isolated ifdtool checkout to the pinned upstream origin")
            isolated_audit = self.artifact_engine.inspect_source(X220_COREBOOT.recipe_id, checkout)
            if not isolated_audit.clean or isolated_audit.tree_id != X220_COREBOOT.source.tree_id:
                raise ValueError("Isolated X220 ifdtool source does not match the pinned clean Coreboot tree")
            compile_tool = subprocess.run(
                [make, "-C", str(checkout / "util" / "ifdtool")],
                capture_output=True, text=True, check=False, timeout=300,
            )
            if compile_tool.returncode != 0:
                raise ValueError("Pinned Coreboot ifdtool could not be built locally; X220 intent remains unavailable")
            ifdtool = checkout / "util" / "ifdtool" / "ifdtool"
            if os.name == "nt":
                ifdtool = ifdtool.with_suffix(".exe")
            if not ifdtool.is_file() or ifdtool.is_symlink():
                raise ValueError("Pinned Coreboot build did not produce a safe ifdtool executable")
            with tempfile.TemporaryDirectory(prefix="librecore-x220-region-") as read_dir:
                dump = Path(read_dir) / "observed-original.rom"
                result = self.transport.read_chip(probe.programmer, probe, dump)
                if not result.success or not dump.is_file() or dump.stat().st_size != probe.capacity_bytes:
                    raise ValueError("Pre-backup X220 read-only region capture failed; no workflow intent was created")
                tool_result = subprocess.run(
                    [str(ifdtool), "-d", str(dump)], capture_output=True, text=True, check=False, timeout=30
                )
                if tool_result.returncode != 0:
                    raise ValueError("Pinned Coreboot ifdtool could not inspect the observed original chip image")
                self._last_x220_region_tool = {
                    "repository_url": X220_COREBOOT.source.repository_url,
                    "revision": X220_COREBOOT.source.revision,
                    "tree_id": X220_COREBOOT.source.tree_id,
                    "tool_sha256": sha256_file(ifdtool),
                    "probe_id": probe.probe_id,
                    "original_capture_sha256": sha256_file(dump),
                }
                return parse_ifdtool_regions(tool_result.stdout + tool_result.stderr, probe.capacity_bytes)

    @staticmethod
    def _programmer(value: object) -> ProgrammerType:
        if not isinstance(value, str):
            raise ValueError("An explicitly selected external programmer is required")
        if value == ProgrammerType.CH341A.value:
            return ProgrammerType.CH341A
        if value == ProgrammerType.RASPBERRY_PI.value:
            return ProgrammerType.RASPBERRY_PI
        if value == ProgrammerType.BUS_PIRATE.value:
            return ProgrammerType.BUS_PIRATE
        raise ValueError("Internal or unknown programmer paths are not available in X200/X220 V1")

    @classmethod
    def _parse_request(cls, document: str) -> dict[str, Any]:
        try:
            value: Any = json.loads(document)
        except json.JSONDecodeError as exc:
            raise ValueError("Operator preparation request is malformed JSON") from exc
        required = {
            "version", "actor", "attested_at", "programmer", "measured_voltage_mv",
            "interface_verified", "wiring_verified", "instrument_id", "measurement_test_point",
            "primary_directory", "recovery_directory",
            "primary_persistent_confirmed", "recovery_persistent_confirmed",
            "source_directory", "output_root", "cpu_count",
        }
        if not isinstance(value, dict) or set(value) != required or value.get("version") != 1:
            raise ValueError("Operator request must use the exact version 1 X200/X220 schema")
        if not isinstance(value["actor"], str) or not value["actor"].strip():
            raise ValueError("A human actor name is required for measured SPI setup evidence")
        if not isinstance(value["attested_at"], str):
            raise ValueError("Measured SPI setup requires a timezone-aware UTC attestation timestamp")
        try:
            attested_at = datetime.fromisoformat(value["attested_at"])
        except ValueError as exc:
            raise ValueError("Measured SPI setup timestamp is malformed") from exc
        if attested_at.tzinfo is None or attested_at.utcoffset() != timedelta(0):
            raise ValueError("Measured SPI setup timestamp must be timezone-aware UTC")
        if attested_at > datetime.now(timezone.utc) + timedelta(seconds=30):
            raise ValueError("Measured SPI setup timestamp cannot be in the future")
        if not isinstance(value["instrument_id"], str) or not value["instrument_id"].strip():
            raise ValueError("A human-reported measurement instrument identifier is required")
        if value["measurement_test_point"] != "chip_vcc_and_programmer_vcc":
            raise ValueError("Voltage must be attested at the chip VCC and programmer VCC test points")
        if type(value["measured_voltage_mv"]) is not int or value["measured_voltage_mv"] != 3300:
            raise ValueError("A human measurement of exactly 3300 mV is required")
        if value["interface_verified"] is not True or value["wiring_verified"] is not True:
            raise ValueError("Programmer interface and wiring require explicit true attestations")
        if value["primary_persistent_confirmed"] is not True or value["recovery_persistent_confirmed"] is not True:
            raise ValueError("Both separate backup destinations require explicit persistence confirmations")
        if type(value["cpu_count"]) is not int or not 1 <= value["cpu_count"] <= 256:
            raise ValueError("cpu_count must be from 1 through 256")
        cls._programmer(value["programmer"])
        for key in ("primary_directory", "recovery_directory", "source_directory"):
            if not isinstance(value[key], str) or not Path(value[key]).is_absolute():
                raise ValueError(f"{key} must be an absolute path")
        if not isinstance(value["output_root"], str) or not Path(value["output_root"]).is_absolute():
            raise ValueError("output_root must be an absolute path")
        if any(
            cls._has_link_component(Path(value[key]), Path(Path(value[key]).anchor))
            for key in ("primary_directory", "recovery_directory", "source_directory", "output_root")
        ):
            raise ValueError("Operator-selected paths must not contain symlinks or junctions")
        for key in ("primary_directory", "recovery_directory"):
            if not Path(value[key]).is_dir():
                raise ValueError(f"{key} must name an existing directory")
        if not Path(value["source_directory"]).is_dir():
            raise ValueError("source_directory must name an existing pinned checkout")
        if Path(value["primary_directory"]).resolve() == Path(value["recovery_directory"]).resolve():
            raise ValueError("Primary and recovery backup destinations must be separate directories")
        return value

    def _checkpoint_record(
        self,
        profile_id: str,
        request: dict[str, Any],
        capture: T430DetectionCapture,
        probe: ChipProbeEvidence,
        intent: BuildIntentEvidence,
    ) -> dict[str, Any]:
        return {
            "schema": _CHECKPOINT_SCHEMA,
            "version": _CHECKPOINT_VERSION,
            "profile_id": profile_id,
            "intent_sha256": build_intent_sha256(intent),
            "device_capture_id": capture.capture_id,
            "device_fingerprint_sha256": capture.device_fingerprint_sha256,
            "probe": {
                "probe_id": probe.probe_id,
                "chip_name": probe.chip_name,
                "capacity_bytes": probe.capacity_bytes,
                "raw_output_sha256": probe.raw_output_sha256,
                "tool_name": probe.tool.name,
                "tool_version": probe.tool.version,
                "tool_executable": probe.tool.executable,
            },
            "operator": LocalWorkflowEvidenceAdapter._operator_record(request),
            "x220_region_tool": self._last_x220_region_tool,
            "backup": None,
            "artifact_manifest": None,
        }

    @staticmethod
    def _canonical(value: dict[str, Any]) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @classmethod
    def _write_checkpoint(cls, path: str | Path, body: dict[str, Any]) -> None:
        target = Path(path).expanduser()
        if not target.is_absolute() or cls._has_link_component(target, Path(target.anchor)):
            raise ValueError("Workflow session checkpoint path must be absolute and link-free")
        parent = target.parent.resolve(strict=True)
        if not parent.is_dir():
            raise ValueError("Workflow session checkpoint parent must be an existing link-free directory")
        document = {**body, "checkpoint_sha256": hashlib.sha256(cls._canonical(body)).hexdigest()}
        temporary = target.with_name(target.name + ".tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ValueError("Workflow session temporary path already exists")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(document, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def _read_checkpoint(cls, path: str | Path, profile_id: str) -> dict[str, Any]:
        target = Path(path).expanduser()
        if not target.is_absolute() or cls._has_link_component(target, Path(target.anchor)) or not target.is_file():
            raise ValueError("Workflow session checkpoint must be an existing absolute regular link-free file")
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Workflow session checkpoint is unreadable") from exc
        if not isinstance(document, dict):
            raise ValueError("Workflow session checkpoint must be an object")
        digest = document.pop("checkpoint_sha256", None)
        if document.get("schema") != _CHECKPOINT_SCHEMA or document.get("version") != _CHECKPOINT_VERSION:
            raise ValueError("Workflow session checkpoint has an unsupported schema or version")
        if document.get("profile_id") != profile_id:
            raise ValueError("Workflow checkpoint profile does not match the selected profile")
        if not isinstance(digest, str) or hashlib.sha256(cls._canonical(document)).hexdigest() != digest:
            raise ValueError("Workflow session checkpoint checksum is invalid")
        return document

    @staticmethod
    def _is_within(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    @staticmethod
    def _has_link_component(path: Path, stop: Path) -> bool:
        """Reject symlinks/junctions in every existing component of a path."""
        absolute = Path(os.path.abspath(path))
        del stop  # Callers pass the anchor; inspect the whole absolute path regardless.
        boundary = Path(absolute.anchor)
        relative = absolute.relative_to(boundary)
        current = boundary
        candidates = [current]
        for part in relative.parts:
            current = current / part
            candidates.append(current)
        for candidate in candidates:
            try:
                if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
                    return True
            except OSError:
                return True
        return False

    @staticmethod
    def _require_same_intent(
        expected: BuildIntentEvidence,
        current: BuildIntentEvidence,
        saved: dict[str, Any],
        probe: ChipProbeEvidence,
    ) -> None:
        saved_probe = saved.get("probe")
        if not isinstance(saved_probe, dict) or (
            build_intent_sha256(expected) != build_intent_sha256(current)
            or saved.get("intent_sha256") != build_intent_sha256(current)
            or saved_probe.get("raw_output_sha256") != probe.raw_output_sha256
            or saved_probe.get("chip_name") != probe.chip_name
            or saved_probe.get("capacity_bytes") != probe.capacity_bytes
            or saved_probe.get("tool_name") != probe.tool.name
            or saved_probe.get("tool_version") != probe.tool.version
            or saved_probe.get("tool_executable") != probe.tool.executable
        ):
            raise ValueError("Fresh local evidence no longer matches the validated workflow intent")

    @staticmethod
    def _required_saved_text(value: dict[str, Any], key: str) -> str:
        result = value.get(key)
        if not isinstance(result, str) or not result:
            raise ValueError(f"Workflow checkpoint backup field {key} is missing or malformed")
        return result

    @staticmethod
    def _operator_record(request: dict[str, Any]) -> dict[str, Any]:
        return {
            key: request[key]
            for key in (
                "actor", "attested_at", "programmer", "measured_voltage_mv",
                    "interface_verified", "wiring_verified", "instrument_id", "measurement_test_point", "primary_directory",
                "recovery_directory", "primary_persistent_confirmed",
                "recovery_persistent_confirmed", "source_directory", "output_root", "cpu_count",
            )
        }

