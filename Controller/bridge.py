import time
import uuid
from typing import Any

import requests


class RimWorldBridgeError(RuntimeError):
    pass


class CommandStatusTimeout(RuntimeError):
    def __init__(self, command_id: str, status: dict[str, Any], elapsed: float) -> None:
        self.command_id = command_id
        self.status = status
        self.elapsed = elapsed
        super().__init__(f"Command {command_id} did not complete within {elapsed:.2f}s")


class CommandStatusUnreachable(RuntimeError):
    def __init__(self, command_id: str, error: str, elapsed: float) -> None:
        self.command_id = command_id
        self.error = error
        self.elapsed = elapsed
        super().__init__(f"Could not verify command {command_id} status after {elapsed:.2f}s: {error}")


class RimWorldBridge:
    MAX_MAP_REGION_SIZE = 40

    def __init__(self, base_url: str = "http://127.0.0.1:47831", timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def health(self) -> dict[str, Any]:
        return self._request_json("GET", "/health")

    def get_state(self) -> dict[str, Any]:
        return self._request_json("GET", "/state")

    def wait_for_state_after(self, after_version: int, timeout_ms: int = 2000) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/state",
            params={"afterVersion": after_version, "timeoutMs": timeout_ms},
        )

    def inspect_map(self, min_x: int, min_z: int, max_x: int, max_z: int) -> dict[str, Any]:
        width = abs(max_x - min_x) + 1
        height = abs(max_z - min_z) + 1
        if width > self.MAX_MAP_REGION_SIZE or height > self.MAX_MAP_REGION_SIZE:
            return {
                "error": "regionTooLarge",
                "maxWidth": self.MAX_MAP_REGION_SIZE,
                "maxHeight": self.MAX_MAP_REGION_SIZE,
                "requestedWidth": width,
                "requestedHeight": height,
            }
        return self._request_json(
            "GET",
            "/map/region",
            params={"minX": min_x, "minZ": min_z, "maxX": max_x, "maxZ": max_z},
        )

    def inspect_room_at(self, x: int, z: int) -> dict[str, Any]:
        return self._request_json("GET", "/room/at", params={"x": x, "z": z})

    def diagnose_construction(self, thing_id: str) -> dict[str, Any]:
        return self._request_json("GET", "/construction/diagnose", params={"thingId": thing_id})

    def find_resource_sources(self, resource_def: str) -> dict[str, Any]:
        return self._request_json("GET", "/resources/sources", params={"resourceDef": resource_def})

    def list_build_options(self, category: str | None = None, search: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if category:
            params["category"] = category
        if search:
            params["search"] = search
        return self._request_json("GET", "/build/options", params=params)

    def get_build_info(self, def_name: str) -> dict[str, Any]:
        return self._request_json("GET", "/build/info", params={"defName": def_name})

    def check_build_placements(self, placements: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request_json("POST", "/build/check", json={"placements": placements})

    def check_zone_placement(
        self,
        zone_type: str,
        min_x: int,
        min_z: int,
        max_x: int,
        max_z: int,
    ) -> dict[str, Any]:
        return self._request_json(
            "GET",
            "/zone/check",
            params={
                "zoneType": zone_type,
                "minX": min_x,
                "minZ": min_z,
                "maxX": max_x,
                "maxZ": max_z,
            },
        )

    def list_growable_plants(self) -> dict[str, Any]:
        return self._request_json("GET", "/growable-plants")

    def list_recipes(self, worktable_id: str) -> dict[str, Any]:
        return self._request_json("GET", "/recipes", params={"worktableId": worktable_id})

    def send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", "/command", json=command)

    def get_command_status(self, command_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/command/{command_id}")

    def submit_command(self, command: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        command = dict(command)
        command_id = str(command.get("commandId") or uuid.uuid4().hex)
        command["commandId"] = command_id

        try:
            accepted = self.send_command(command)
            accepted_command_id = accepted.get("commandId")
            if not accepted_command_id:
                raise RimWorldBridgeError(f"Bridge accepted response did not include commandId: {accepted}")
            if str(accepted_command_id) != command_id:
                raise RimWorldBridgeError(
                    f"Bridge returned mismatched commandId: sent {command_id}, got {accepted_command_id}"
                )
        except RimWorldBridgeError as exc:
            elapsed = time.monotonic() - started
            try:
                status = self.get_command_status(command_id)
            except RimWorldBridgeError:
                raise CommandStatusUnreachable(command_id, str(exc), elapsed) from exc
            return {
                "accepted": True,
                "commandId": command_id,
                "command": command,
                "startedAt": started,
                "submitElapsedSeconds": round(elapsed, 3),
                "submitUncertain": True,
                "status": status,
            }

        return {
            "accepted": True,
            "commandId": command_id,
            "command": command,
            "startedAt": started,
            "submitElapsedSeconds": round(time.monotonic() - started, 3),
        }

    def submit_commands(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.submit_command(command) for command in commands]

    def wait_for_commands(
        self,
        submissions: list[dict[str, Any]],
        timeout: float = 5.0,
        poll_interval: float = 0.25,
    ) -> dict[str, dict[str, Any]]:
        pending: dict[str, dict[str, Any]] = {str(item["commandId"]): item for item in submissions}
        results: dict[str, dict[str, Any]] = {}
        last_errors: dict[str, str] = {}
        deadline = time.monotonic() + timeout

        while pending and time.monotonic() < deadline:
            for command_id in list(pending):
                try:
                    status = self.get_command_status(command_id)
                except RimWorldBridgeError as exc:
                    last_errors[command_id] = str(exc)
                    continue

                if status.get("status") == "completed":
                    status["elapsedSeconds"] = round(time.monotonic() - pending[command_id]["startedAt"], 3)
                    results[command_id] = status
                    del pending[command_id]

            if pending:
                time.sleep(poll_interval)

        for command_id, submission in list(pending.items()):
            elapsed = time.monotonic() - submission["startedAt"]
            try:
                final_status = self.get_command_status(command_id)
            except RimWorldBridgeError as exc:
                results[command_id] = {
                    "commandId": command_id,
                    "status": "unknown",
                    "success": None,
                    "uncertain": True,
                    "error": last_errors.get(command_id, str(exc)),
                    "elapsedSeconds": round(elapsed, 3),
                }
                continue

            final_status["elapsedSeconds"] = round(time.monotonic() - submission["startedAt"], 3)
            if final_status.get("status") == "completed":
                results[command_id] = final_status
            elif final_status.get("status") == "queued":
                final_status["success"] = None
                final_status["uncertain"] = True
                results[command_id] = final_status
            else:
                final_status["success"] = None
                final_status["uncertain"] = True
                results[command_id] = final_status

        return results

    def send_command_and_wait(
        self,
        command: dict[str, Any],
        timeout: float = 5.0,
        poll_interval: float = 0.25,
    ) -> dict[str, Any]:
        submission = self.submit_command(command)
        command_id = str(submission["commandId"])
        results = self.wait_for_commands([submission], timeout=timeout, poll_interval=poll_interval)
        result = results[command_id]
        return result

    def reconcile_command(self, command_id: str, started_at: float | None = None) -> dict[str, Any]:
        status = self.get_command_status(command_id)
        if started_at is not None:
            status["elapsedSeconds"] = round(time.monotonic() - started_at, 3)
        return status

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            data = response.json()
        except requests.ConnectionError as exc:
            raise RimWorldBridgeError(f"Could not connect to RimGPT bridge at {self.base_url}") from exc
        except requests.Timeout as exc:
            raise RimWorldBridgeError(f"Timed out calling RimGPT bridge endpoint {path}") from exc
        except requests.HTTPError as exc:
            body = exc.response.text if exc.response is not None else ""
            raise RimWorldBridgeError(f"Bridge returned HTTP error for {path}: {body}") from exc
        except ValueError as exc:
            raise RimWorldBridgeError(f"Bridge returned non-JSON response for {path}") from exc
        except requests.RequestException as exc:
            raise RimWorldBridgeError(f"Bridge request failed for {path}: {exc}") from exc

        if not isinstance(data, dict):
            raise RimWorldBridgeError(f"Bridge returned unexpected JSON for {path}: {data!r}")
        return data
