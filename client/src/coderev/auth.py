"""GitHub token resolution."""

import os
import subprocess


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


def claim_auth_token(base_url: str) -> str:
    """Claim the one-time auth token from the coderev server."""
    import httpx

    resp = httpx.post(f"{base_url}/auth-token", timeout=10)
    if resp.status_code == 410:
        raise RuntimeError("Auth token already claimed by another client")
    resp.raise_for_status()
    return resp.json()["token"]
