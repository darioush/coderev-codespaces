"""Manage codespace port visibility and resolve the public URL."""

import subprocess

from coderev.config import SERVER_PORT


def make_port_public(codespace_name: str, port: int = SERVER_PORT) -> None:
    """Set the codespace port visibility to public."""
    result = subprocess.run(
        [
            "gh", "codespace", "ports", "visibility",
            f"{port}:public",
            "-c", codespace_name,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to make port {port} public: {result.stderr.strip()}"
        )


def get_public_url(codespace_name: str, port: int = SERVER_PORT) -> str:
    """Get the public HTTPS URL for a codespace port."""
    result = subprocess.run(
        [
            "gh", "codespace", "ports",
            "-c", codespace_name,
            "--json", "label,sourcePort,browseUrl",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to list ports: {result.stderr.strip()}"
        )

    import json
    ports = json.loads(result.stdout)
    for p in ports:
        if p.get("sourcePort") == port:
            return p["browseUrl"].rstrip("/")

    # Fallback: construct from codespace name
    return f"https://{codespace_name}-{port}.app.github.dev"
