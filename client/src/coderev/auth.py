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


def get_codespace_auth_token(codespace_name: str) -> str:
    """Retrieve the coderev bearer token from inside a codespace."""
    result = subprocess.run(
        [
            "gh", "codespace", "ssh",
            "-c", codespace_name,
            "--", "cat", "/tmp/coderev-auth-token",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to fetch auth token from codespace: {result.stderr.strip()}"
        )
    return result.stdout.strip()
