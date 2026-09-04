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
    def __init__(self, base_url: str = "http://127.0.0.1:47831", timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def health(self) -> dict[str, Any]:
        return self._request_json("GET", "/health")

    def get_state(self) -> dict[str, Any]:
        return self._request_json("GET", "/state")

    def send_command(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", "/command", json=command)

    def get_command_status(self, command_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/command/{command_id}")

    def send_command_and_wait(
        self,
        command: dict[str, Any],
        timeout: float = 5.0,
        poll_interval: float = 0.25,
    ) -> dict[str, Any]:
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
            if status.get("status") == "completed":
                status["elapsedSeconds"] = round(time.monotonic() - started, 3)
                return status
            status["commandId"] = command_id
            status["elapsedSeconds"] = round(elapsed, 3)
            raise CommandStatusTimeout(command_id, status, elapsed) from exc

        deadline = started + timeout
        while time.monotonic() < deadline:
            try:
                status = self.get_command_status(command_id)
            except RimWorldBridgeError as exc:
                elapsed = time.monotonic() - started
                raise CommandStatusUnreachable(command_id, str(exc), elapsed) from exc

            if status.get("status") == "completed":
                status["elapsedSeconds"] = round(time.monotonic() - started, 3)
                return status
            time.sleep(poll_interval)

        elapsed = time.monotonic() - started
        try:
            final_status = self.get_command_status(command_id)
        except RimWorldBridgeError as exc:
            raise CommandStatusUnreachable(command_id, str(exc), elapsed) from exc

        if final_status.get("status") == "completed":
            final_status["elapsedSeconds"] = round(time.monotonic() - started, 3)
            return final_status

        final_status["commandId"] = command_id
        final_status["elapsedSeconds"] = round(elapsed, 3)
        raise CommandStatusTimeout(command_id, final_status, elapsed)

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
