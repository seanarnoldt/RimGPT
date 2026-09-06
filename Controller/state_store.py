"""Durable local ownership of complete authoritative RimGPT state snapshots."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from decision_handoff import DecisionHandoffError, validate_handoff
from strategic_memory import (
    StrategicMemoryError,
    apply_update as apply_memory_update,
    empty_memory,
    memory_telemetry,
    reconcile_memory as reconcile_strategic_memory,
    validate_memory,
)


PERSISTENCE_FORMAT_VERSION = 1


class StateStoreError(RuntimeError):
    pass


class StateIdentity:
    def __init__(self, lineage_id: str) -> None:
        self.lineage_id = lineage_id
        self.key = hashlib.sha256(("rimgpt-colony-lineage-v1\0" + lineage_id).encode("utf-8")).hexdigest()[:24]

    def as_dict(self) -> dict[str, str]:
        return {"kind": "rimGPTGameComponentGuid", "colonyLineageId": self.lineage_id}


class StateStore:
    """Own colony-scoped authoritative state, memory, baseline, and decision handoff."""

    def __init__(self, root: str | Path | None = None, logger: Callable[[str], None] | None = None) -> None:
        self.root = Path(root) if root is not None else Path(__file__).resolve().parent / "state"
        self._log = logger or print
        self._identity: StateIdentity | None = None
        self._current_state: dict[str, Any] | None = None
        self._decision_baseline: dict[str, Any] | None = None
        self._memory: dict[str, Any] | None = None
        self._decision_handoff: dict[str, Any] | None = None
        self._loaded_current_from_disk = False

    @property
    def identity(self) -> StateIdentity | None:
        return self._identity

    @property
    def colony_directory(self) -> Path | None:
        return self.root / self._identity.key if self._identity is not None else None

    def get_current_state(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._current_state)

    def get_decision_baseline(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._decision_baseline)

    def get_memory(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._memory)

    def get_decision_handoff(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._decision_handoff)

    def apply_memory_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """Merge and persist a bounded strategic-memory patch for this colony."""
        if self._identity is None or self._memory is None:
            raise StateStoreError("Cannot update strategic memory without a loaded colony identity")
        try:
            updated, changes = apply_memory_update(self._memory, update, self._identity.as_dict())
        except StrategicMemoryError as exc:
            raise StateStoreError(f"Invalid strategic memory update: {exc}") from exc
        self._memory = updated
        if changes:
            self._persist_memory()
            self._log("[MEMORY] applied " + ",".join(changes))
            self._log_memory_telemetry()
        memory = self.get_memory()
        assert memory is not None
        return memory

    def reconcile_memory(self) -> dict[str, Any] | None:
        """Conservatively remove memory proven obsolete by authoritative state."""
        if self._identity is None or self._memory is None:
            return None
        try:
            reconciled, changes = reconcile_strategic_memory(
                self._memory,
                self._current_state,
                self._identity.as_dict(),
            )
        except StrategicMemoryError as exc:
            self._warn(f"could not reconcile strategic memory: {exc}")
            return self.get_memory()
        self._memory = reconciled
        if changes:
            self._persist_memory()
            self._log("[MEMORY] reconciled " + ",".join(changes))
            self._log_memory_telemetry()
        return self.get_memory()

    def get_changes_since_last_decision(self) -> dict[str, Any]:
        """Calculate a side-effect-free semantic delta for future prompt use."""
        from state_diff import StateDiff, serialized_chars

        baseline = self.get_decision_baseline()
        current = self.get_current_state()
        delta = StateDiff.compare(baseline, current, logger=self._log)
        delta_chars = serialized_chars(delta)
        full_state_chars = serialized_chars(current) if current is not None else 0
        ratio = (full_state_chars / delta_chars) if delta_chars else 0.0
        self._log(
            f"[DELTA] from={snapshot_version(baseline)} to={snapshot_version(current)} "
            f"chars={delta_chars} fullStateChars={full_state_chars} compressionRatio={ratio:.1f}x"
        )
        return delta

    def update_current_state(self, snapshot: dict[str, Any]) -> bool:
        """Accept a live authoritative state and persist it when it advances."""
        if not isinstance(snapshot, dict):
            raise StateStoreError("Authoritative state must be a JSON object")

        identity = identity_from_state(snapshot)
        if identity is None:
            self._identity = None
            self._current_state = copy.deepcopy(snapshot)
            self._decision_baseline = None
            self._memory = None
            self._decision_handoff = None
            self._loaded_current_from_disk = False
            self._warn("live state has no loaded colony identity; not persisting it")
            return False

        schema = state_schema_version(snapshot)
        if schema is None:
            self._warn("live state has no usable schemaVersion; not persisting it")
            return False

        if self._identity is None or self._identity.key != identity.key:
            if self._identity is not None:
                self._log("[STATESTORE] Colony identity changed; persisted baseline will not be reused")
            self._activate(identity, schema)

        current_schema = state_schema_version(self._current_state) if self._current_state is not None else None
        if current_schema is not None and current_schema != schema:
            self._log("[STATESTORE] schema mismatch; invalidated decision baseline")
            self.clear_decision_baseline()
            self._current_state = None

        incoming_version = snapshot_version(snapshot)
        current_version = snapshot_version(self._current_state) if self._current_state is not None else None
        if current_version is not None and incoming_version is not None:
            if incoming_version == current_version:
                self._loaded_current_from_disk = False
                return False
            if incoming_version < current_version:
                if not self._loaded_current_from_disk:
                    self._log(
                        f"[STATESTORE] ignored older snapshot {incoming_version}; current={current_version}"
                    )
                    return False
                self._log("[STATESTORE] snapshot lineage reset or ambiguous; using fresh live state")
                self.clear_decision_baseline()

        self._current_state = copy.deepcopy(snapshot)
        self._loaded_current_from_disk = False
        self._persist_current()
        self.reconcile_memory()
        short_key = self._identity.key[:8] if self._identity is not None else "unknown"
        self._log(f"[STATESTORE] colony={short_key} snapshot={incoming_version if incoming_version is not None else 'unknown'} persisted")
        return True

    def set_decision_baseline(self, snapshot: dict[str, Any]) -> None:
        if self._identity is None:
            raise StateStoreError("Cannot set a decision baseline without a loaded colony identity")
        if identity_from_state(snapshot) is None or identity_from_state(snapshot).key != self._identity.key:
            raise StateStoreError("Decision baseline belongs to a different colony identity")
        if state_schema_version(snapshot) != state_schema_version(self._current_state):
            raise StateStoreError("Decision baseline schema does not match current authoritative state")

        self._decision_baseline = copy.deepcopy(snapshot)
        self._atomic_write_json(self._baseline_path(), self._baseline_envelope(snapshot))
        self._log("[STATESTORE] decision baseline persisted")

    def commit_successful_decision(self, snapshot: dict[str, Any], handoff: dict[str, Any]) -> None:
        """Persist baseline and handoff only after all completion checks pass."""
        if self._identity is None:
            raise StateStoreError("Cannot commit a decision without a loaded colony identity")
        snapshot_identity = identity_from_state(snapshot)
        if snapshot_identity is None or snapshot_identity.key != self._identity.key:
            raise StateStoreError("Successful decision belongs to a different colony identity")
        if state_schema_version(snapshot) != state_schema_version(self._current_state):
            raise StateStoreError("Decision baseline schema does not match current authoritative state")
        try:
            validated_handoff = validate_handoff(handoff)
        except DecisionHandoffError as exc:
            raise StateStoreError(f"Invalid decision handoff: {exc}") from exc

        previous_baseline = copy.deepcopy(self._decision_baseline)
        previous_handoff = copy.deepcopy(self._decision_handoff)
        try:
            self._atomic_write_json(self._handoff_path(), self._handoff_envelope(validated_handoff))
            self._atomic_write_json(self._baseline_path(), self._baseline_envelope(snapshot))
        except OSError as exc:
            self._restore_decision_artifact(self._handoff_path(), previous_handoff, handoff=True)
            self._restore_decision_artifact(self._baseline_path(), previous_baseline, handoff=False)
            raise StateStoreError(f"Could not atomically commit successful decision: {exc}") from exc
        self._decision_handoff = copy.deepcopy(validated_handoff)
        self._decision_baseline = copy.deepcopy(snapshot)
        self._log("[HANDOFF] successful decision handoff and baseline persisted")

    def clear_decision_baseline(self) -> None:
        self._decision_baseline = None
        path = self._baseline_path() if self._identity is not None else None
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                self._warn(f"could not clear decision baseline: {exc}")

    def _activate(self, identity: StateIdentity, expected_schema: int) -> None:
        self._identity = identity
        self._current_state = None
        self._decision_baseline = None
        self._memory = None
        self._decision_handoff = None
        self._loaded_current_from_disk = False
        directory = self.colony_directory
        assert directory is not None
        directory.mkdir(parents=True, exist_ok=True)
        self._load_memory(identity)
        self._load_decision_handoff(identity)

        metadata = self._read_json(self._metadata_path(), "metadata")
        if metadata is None:
            return
        if not self._valid_metadata(metadata, identity, expected_schema):
            self._warn("persisted metadata invalid or incompatible; starting with fresh authoritative baseline")
            self._quarantine(self._metadata_path())
            return

        state = self._read_json(self._current_state_path(), "current state")
        if state is None or not self._valid_state(state, identity, expected_schema, metadata):
            self._warn("persisted state invalid; starting with fresh authoritative baseline")
            self._quarantine(self._current_state_path())
            return

        self._current_state = state
        self._loaded_current_from_disk = True
        self._log(f"[STATESTORE] loaded persisted state snapshot={snapshot_version(state)}")

        baseline = self._read_json(self._baseline_path(), "decision baseline", warn_missing=False)
        if baseline is not None:
            baseline_state = baseline.get("state") if isinstance(baseline, dict) else None
            if self._valid_baseline(baseline, baseline_state, identity, expected_schema):
                self._decision_baseline = baseline_state
            else:
                self._log("[STATESTORE] schema/identity mismatch; invalidated decision baseline")
                self._quarantine(self._baseline_path())

    def _persist_current(self) -> None:
        if self._identity is None or self._current_state is None:
            return
        self._atomic_write_json(self._current_state_path(), self._current_state)
        self._atomic_write_json(self._metadata_path(), self._metadata_for(self._current_state))

    def _persist_memory(self) -> None:
        if self._identity is None or self._memory is None:
            return
        self._atomic_write_json(self._memory_path(), self._memory)

    def _load_decision_handoff(self, identity: StateIdentity) -> None:
        path = self._handoff_path()
        envelope = self._read_json(path, "decision handoff", warn_missing=False)
        if envelope is None:
            self._decision_handoff = None
            return
        try:
            if not isinstance(envelope, dict) or envelope.get("persistenceFormatVersion") != PERSISTENCE_FORMAT_VERSION:
                raise DecisionHandoffError("invalid persistence format")
            if envelope.get("identity") != identity.as_dict():
                raise DecisionHandoffError("colony identity mismatch")
            self._decision_handoff = validate_handoff(envelope.get("handoff"))
        except DecisionHandoffError as exc:
            self._warn(f"invalid decision handoff: {exc}")
            self._quarantine(path)
            self._decision_handoff = None

    def _load_memory(self, identity: StateIdentity) -> None:
        path = self._memory_path()
        existed = path.exists()
        document = self._read_json(path, "strategic memory", warn_missing=False)
        if document is None:
            self._memory = empty_memory(identity.as_dict())
            if existed:
                self._log("[MEMORY] Invalid persisted strategic memory; initialized clean memory")
            return
        try:
            self._memory = validate_memory(document, identity.as_dict())
        except StrategicMemoryError as exc:
            self._warn(f"invalid strategic memory: {exc}")
            self._quarantine(path)
            self._memory = empty_memory(identity.as_dict())
            self._log("[MEMORY] Invalid persisted strategic memory; initialized clean memory")

    def _metadata_for(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        assert self._identity is not None
        game = snapshot.get("game") if isinstance(snapshot.get("game"), dict) else {}
        return {
            "persistenceFormatVersion": PERSISTENCE_FORMAT_VERSION,
            "identity": self._identity.as_dict(),
            "stateSchemaVersion": state_schema_version(snapshot),
            "snapshotVersion": snapshot_version(snapshot),
            "ticksGame": snapshot_ticks(snapshot),
            "currentMapId": game.get("currentMapId"),
            "persistedAtUtc": utc_now(),
        }

    def _baseline_envelope(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {"metadata": self._metadata_for(snapshot), "state": copy.deepcopy(snapshot)}

    def _handoff_envelope(self, handoff: dict[str, Any]) -> dict[str, Any]:
        assert self._identity is not None
        return {
            "persistenceFormatVersion": PERSISTENCE_FORMAT_VERSION,
            "identity": self._identity.as_dict(),
            "handoff": copy.deepcopy(handoff),
        }

    def _valid_metadata(self, metadata: Any, identity: StateIdentity, expected_schema: int) -> bool:
        return (
            isinstance(metadata, dict)
            and metadata.get("persistenceFormatVersion") == PERSISTENCE_FORMAT_VERSION
            and metadata.get("identity") == identity.as_dict()
            and metadata.get("stateSchemaVersion") == expected_schema
        )

    def _valid_state(self, state: Any, identity: StateIdentity, expected_schema: int, metadata: dict[str, Any]) -> bool:
        return (
            isinstance(state, dict)
            and identity_from_state(state) is not None
            and identity_from_state(state).key == identity.key
            and state_schema_version(state) == expected_schema
            and snapshot_version(state) == metadata.get("snapshotVersion")
        )

    def _valid_baseline(self, envelope: Any, state: Any, identity: StateIdentity, expected_schema: int) -> bool:
        return (
            isinstance(envelope, dict)
            and self._valid_metadata(envelope.get("metadata"), identity, expected_schema)
            and self._valid_state(state, identity, expected_schema, envelope["metadata"])
        )

    def _metadata_path(self) -> Path:
        assert self.colony_directory is not None
        return self.colony_directory / "metadata.json"

    def _current_state_path(self) -> Path:
        assert self.colony_directory is not None
        return self.colony_directory / "current_state.json"

    def _baseline_path(self) -> Path:
        assert self.colony_directory is not None
        return self.colony_directory / "decision_baseline.json"

    def _memory_path(self) -> Path:
        assert self.colony_directory is not None
        return self.colony_directory / "memory.json"

    def _handoff_path(self) -> Path:
        assert self.colony_directory is not None
        return self.colony_directory / "decision_handoff.json"

    def _restore_decision_artifact(self, path: Path, value: dict[str, Any] | None, *, handoff: bool) -> None:
        try:
            if value is None:
                path.unlink(missing_ok=True)
            else:
                envelope = self._handoff_envelope(value) if handoff else self._baseline_envelope(value)
                self._atomic_write_json(path, envelope)
        except OSError as exc:
            self._warn(f"could not restore {path.name} after failed decision commit: {exc}")

    def _log_memory_telemetry(self) -> None:
        if self._memory is None:
            return
        telemetry = memory_telemetry(self._memory)
        self._log(
            "[MEMORY] "
            f"chars={telemetry['chars']} approxTokens={telemetry['approxTokens']} "
            f"currentGoals={telemetry['currentGoals']} nextPriorities={telemetry['nextPriorities']} "
            f"longTermGoals={telemetry['longTermGoals']} decisions={telemetry['decisions']} "
            f"unresolvedProblems={telemetry['unresolvedProblems']} locations={telemetry['locations']} "
            f"pawnRoles={telemetry['pawnRoles']}"
        )

    def _read_json(self, path: Path, description: str, warn_missing: bool = True) -> Any | None:
        if not path.exists():
            if warn_missing and description == "metadata":
                self._log("[STATESTORE] new colony identity; initialized fresh store")
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            self._warn(f"corrupt {description}; starting fresh ({exc})")
            self._quarantine(path)
            return None

    def _atomic_write_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _quarantine(self, path: Path) -> None:
        if not path.exists():
            return
        quarantine = path.with_name(path.name + ".invalid-" + utc_now().replace(":", "") + "-" + uuid4().hex[:8])
        try:
            path.replace(quarantine)
        except OSError as exc:
            self._warn(f"could not quarantine {path.name}: {exc}")

    def _warn(self, message: str) -> None:
        self._log("[STATESTORE] " + message)


def identity_from_state(snapshot: dict[str, Any]) -> StateIdentity | None:
    game = snapshot.get("game")
    if not isinstance(game, dict) or game.get("loaded") is not True:
        return None
    lineage_id = game.get("colonyLineageId")
    return StateIdentity(lineage_id) if isinstance(lineage_id, str) and lineage_id else None


def state_schema_version(snapshot: dict[str, Any] | None) -> int | None:
    if not isinstance(snapshot, dict):
        return None
    value = snapshot.get("schemaVersion")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def snapshot_version(snapshot: dict[str, Any] | None) -> int | None:
    if not isinstance(snapshot, dict):
        return None
    data = snapshot.get("snapshot")
    value = data.get("version") if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def snapshot_ticks(snapshot: dict[str, Any]) -> int | None:
    data = snapshot.get("snapshot")
    value = data.get("ticksGame") if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
