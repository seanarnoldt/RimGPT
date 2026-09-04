import time
from typing import Any

import requests


class RimWorldBridgeError(RuntimeError):
    pass


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
        timeout: float = 15.0,
        poll_interval: float = 0.25,
    ) -> dict[str, Any]:
        accepted = self.send_command(command)
        command_id = accepted.get("commandId")
        if not command_id:
            raise RimWorldBridgeError(f"Bridge accepted response did not include commandId: {accepted}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.get_command_status(str(command_id))
            if status.get("status") == "completed":
                return status
            time.sleep(poll_interval)

        raise RimWorldBridgeError(f"Timed out waiting for command {command_id}")

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
