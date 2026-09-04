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
    """Persists only authoritative snapshots; prompting and diffing stay elsewhere."""

    def __init__(self, root: str | Path | None = None, logger: Callable[[str], None] | None = None) -> None:
        self.root = Path(root) if root is not None else Path(__file__).resolve().parent / "state"
        self._log = logger or print
        self._identity: StateIdentity | None = None
        self._current_state: dict[str, Any] | None = None
        self._decision_baseline: dict[str, Any] | None = None
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

    def update_current_state(self, snapshot: dict[str, Any]) -> bool:
        """Accept a live authoritative state and persist it when it advances."""
        if not isinstance(snapshot, dict):
            raise StateStoreError("Authoritative state must be a JSON object")

        identity = identity_from_state(snapshot)
        if identity is None:
            self._identity = None
            self._current_state = copy.deepcopy(snapshot)
            self._decision_baseline = None
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
        self._loaded_current_from_disk = False
        directory = self.colony_directory
        assert directory is not None
        directory.mkdir(parents=True, exist_ok=True)

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
