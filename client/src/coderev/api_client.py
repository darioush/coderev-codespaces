"""HTTP client for the coderev API server running in a codespace."""

import time

import httpx

from coderev.config import ASK_TIMEOUT, HEALTH_POLL_INTERVAL, HEALTH_POLL_TIMEOUT


class ApiClient:
    def __init__(self, base_url: str, auth_token: str):
        self.base_url = base_url
        self.auth_token = auth_token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.auth_token}"}

    def wait_until_ready(self) -> dict:
        """Poll /health until the server is up."""
        deadline = time.monotonic() + HEALTH_POLL_TIMEOUT
        last_err = None
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(
                    f"{self.base_url}/health",
                    timeout=5,
                )
                if resp.status_code == 200:
                    return resp.json()
            except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as e:
                last_err = e
            time.sleep(HEALTH_POLL_INTERVAL)
        raise TimeoutError(
            f"Server not ready within {HEALTH_POLL_TIMEOUT}s. Last error: {last_err}"
        )

    def set_credentials(self, credentials: dict) -> None:
        """POST /credentials to write Claude OAuth creds to the codespace."""
        resp = httpx.post(
            f"{self.base_url}/credentials",
            json=credentials,
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()

    def ask(
        self,
        question: str,
        files: list[str] | None = None,
        diff_range: str | None = None,
        model: str | None = None,
        max_turns: int = 30,
        session_id: str | None = None,
    ) -> dict:
        """POST /ask and return the response dict."""
        payload: dict = {"question": question, "max_turns": max_turns}
        if files:
            payload["files"] = files
        if diff_range:
            payload["diff_range"] = diff_range
        if model:
            payload["model"] = model
        if session_id:
            payload["session_id"] = session_id

        resp = httpx.post(
            f"{self.base_url}/ask",
            json=payload,
            headers=self._headers(),
            timeout=ASK_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def ask_stream(
        self,
        question: str,
        files: list[str] | None = None,
        diff_range: str | None = None,
        model: str | None = None,
        max_turns: int = 10,
    ):
        """POST /ask/stream, yields SSE data lines."""
        payload: dict = {"question": question, "max_turns": max_turns}
        if files:
            payload["files"] = files
        if diff_range:
            payload["diff_range"] = diff_range
        if model:
            payload["model"] = model

        with httpx.stream(
            "POST",
            f"{self.base_url}/ask/stream",
            json=payload,
            headers=self._headers(),
            timeout=ASK_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        return
                    yield data
