"""Manage gh codespace ports forward subprocess."""

import subprocess
import time

from coderev.config import SERVER_PORT


class Tunnel:
    """Wraps `gh codespace ports forward` as a managed subprocess."""

    def __init__(self, codespace_name: str, port: int = SERVER_PORT):
        self.codespace_name = codespace_name
        self.port = port
        self._proc: subprocess.Popen | None = None

    @property
    def local_url(self) -> str:
        return f"http://localhost:{self.port}"

    def open(self) -> None:
        if self._proc and self._proc.poll() is None:
            return  # already running
        self._proc = subprocess.Popen(
            [
                "gh", "codespace", "ports", "forward",
                f"{self.port}:{self.port}",
                "-c", self.codespace_name,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        # Give the tunnel a moment to establish
        time.sleep(3)
        if self._proc.poll() is not None:
            stderr = self._proc.stderr.read().decode() if self._proc.stderr else ""
            raise RuntimeError(f"Tunnel failed to start: {stderr}")

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()
