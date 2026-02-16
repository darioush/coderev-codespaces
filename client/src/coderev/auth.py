"""GitHub token resolution and coderev auth token caching."""

import json
import os
import platform
import subprocess
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "coderev"


def get_github_token() -> str:
    """Resolve a GitHub token from env or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except FileNotFoundError:
        pass

    raise RuntimeError(
        "No GitHub token found. Set GITHUB_TOKEN or run `gh auth login`."
    )


def get_auth_token(base_url: str, codespace_name: str) -> str:
    """Get auth token — try to claim fresh, fall back to cache."""
    # Always try to claim first (works if server just started)
    try:
        token = _claim_auth_token(base_url)
        _save_cached_token(codespace_name, token)
        return token
    except RuntimeError:
        pass  # 410 = already claimed

    # Fall back to cached token
    cached = _load_cached_token(codespace_name)
    if cached:
        return cached

    raise RuntimeError(
        "Auth token already claimed and not in local cache. "
        "Restart the codespace server to generate a new token."
    )


def _claim_auth_token(base_url: str) -> str:
    """Claim the one-time auth token from the coderev server."""
    import httpx

    resp = httpx.post(f"{base_url}/auth-token", timeout=10)
    if resp.status_code == 410:
        raise RuntimeError(
            "Auth token already claimed and not in local cache. "
            "Restart the codespace server to generate a new token."
        )
    resp.raise_for_status()
    return resp.json()["token"]


def _cache_path(codespace_name: str) -> Path:
    return CACHE_DIR / f"{codespace_name}.json"


def _load_cached_token(codespace_name: str) -> str | None:
    path = _cache_path(codespace_name)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return data.get("token")
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _save_cached_token(codespace_name: str, token: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(codespace_name)
    path.write_text(json.dumps({"token": token}))
    path.chmod(0o600)


def get_claude_oauth_credentials() -> dict:
    """Read Claude Code OAuth credentials from local machine.

    On macOS: reads from Keychain ("Claude Code-credentials")
    On Linux: reads from ~/.claude/.credentials.json
    """
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                return data.get("claudeAiOauth", {})
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass
    else:
        cred_path = Path.home() / ".claude" / ".credentials.json"
        if cred_path.exists():
            try:
                data = json.loads(cred_path.read_text())
                return data.get("claudeAiOauth", {})
            except (json.JSONDecodeError, OSError):
                pass

    raise RuntimeError(
        "No Claude Code OAuth credentials found. Run `claude /login` first."
    )
