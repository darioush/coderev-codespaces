"""Codespace lifecycle management via GitHub REST API."""

import time

import httpx

from coderev.config import (
    CODESPACE_BOOT_TIMEOUT,
    CODESPACE_IDLE_TIMEOUT_MINUTES,
    CODESPACE_POLL_INTERVAL,
    MACHINE_TYPE,
)


class CodespaceManager:
    def __init__(self, token: str):
        self.client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )

    def find(self, repo: str, branch: str) -> dict | None:
        """Find an existing codespace for repo+branch."""
        resp = self.client.get(
            "/user/codespaces",
            params={"repository_id": self._repo_id(repo)},
        )
        resp.raise_for_status()
        for cs in resp.json().get("codespaces", []):
            full_repo = cs.get("repository", {}).get("full_name", "")
            cs_branch = cs.get("git_status", {}).get("ref", "")
            if full_repo == repo and cs_branch == branch:
                return cs
        return None

    def create(self, repo: str, branch: str) -> dict:
        """Create a new codespace for repo+branch."""
        resp = self.client.post(
            f"/repos/{repo}/codespaces",
            json={
                "ref": branch,
                "machine": MACHINE_TYPE,
                "idle_timeout_minutes": CODESPACE_IDLE_TIMEOUT_MINUTES,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def start(self, codespace_name: str) -> dict:
        """Start a stopped codespace."""
        resp = self.client.post(
            f"/user/codespaces/{codespace_name}/start",
        )
        resp.raise_for_status()
        return resp.json()

    def stop(self, codespace_name: str) -> dict:
        """Stop a running codespace."""
        resp = self.client.post(
            f"/user/codespaces/{codespace_name}/stop",
        )
        resp.raise_for_status()
        return resp.json()

    def delete(self, codespace_name: str) -> None:
        """Delete a codespace."""
        resp = self.client.delete(f"/user/codespaces/{codespace_name}")
        resp.raise_for_status()

    def list_for_repo(self, repo: str) -> list[dict]:
        """List all codespaces for a given repo."""
        repo_id = self._repo_id(repo)
        resp = self.client.get(
            "/user/codespaces",
            params={"repository_id": repo_id},
        )
        resp.raise_for_status()
        return resp.json().get("codespaces", [])

    def list_all(self) -> list[dict]:
        """List all user codespaces."""
        resp = self.client.get("/user/codespaces")
        resp.raise_for_status()
        return resp.json().get("codespaces", [])

    def wait_until_available(
        self, codespace_name: str, on_poll=None
    ) -> dict:
        """Poll until codespace state is Available."""
        deadline = time.monotonic() + CODESPACE_BOOT_TIMEOUT
        while time.monotonic() < deadline:
            resp = self.client.get(f"/user/codespaces/{codespace_name}")
            resp.raise_for_status()
            cs = resp.json()
            state = cs.get("state", "Unknown")
            if state == "Available":
                return cs
            if on_poll:
                on_poll(state)
            time.sleep(CODESPACE_POLL_INTERVAL)
        raise TimeoutError(
            f"Codespace {codespace_name} did not become Available "
            f"within {CODESPACE_BOOT_TIMEOUT}s"
        )

    def find_or_create(self, repo: str, branch: str, on_status=None) -> str:
        """Find/create/start a codespace. Returns the codespace name."""
        def _emit(msg):
            if on_status:
                on_status(msg)

        cs = self.find(repo, branch)
        if cs:
            state = cs.get("state", "Unknown")
            name = cs["name"]
            if state == "Available":
                _emit(f"Reusing running codespace {name}")
                return name
            if state in ("Shutdown", "ShuttingDown"):
                _emit(f"Starting stopped codespace {name}...")
                self.start(name)
                self.wait_until_available(name, on_poll=lambda s: _emit(f"Codespace {name}: {s}"))
                return name
            _emit(f"Codespace {name} is {state}, waiting...")
            self.wait_until_available(name, on_poll=lambda s: _emit(f"Codespace {name}: {s}"))
            return name

        _emit("Creating new codespace...")
        cs = self.create(repo, branch)
        name = cs["name"]
        _emit(f"Created {name}, waiting for boot...")
        self.wait_until_available(name, on_poll=lambda s: _emit(f"Codespace {name}: {s}"))
        return name

    def _repo_id(self, repo: str) -> int:
        resp = self.client.get(f"/repos/{repo}")
        resp.raise_for_status()
        return resp.json()["id"]
