"""coderev CLI -- ask Claude Code questions about any repo via GitHub Codespaces."""

import json
import sys

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from coderev.api_client import ApiClient
from coderev.auth import claim_auth_token, get_github_token
from coderev.codespace import CodespaceManager
from coderev.tunnel import Tunnel

console = Console()


@click.group()
def cli():
    """Ask Claude Code questions about code on any branch/PR via GitHub Codespaces."""


@cli.command()
@click.argument("repo")
@click.argument("branch")
@click.argument("question")
@click.option("--files", "-f", multiple=True, help="Files to focus on")
@click.option("--diff", "-d", "diff_range", help="Git diff range (e.g. main..HEAD)")
@click.option("--model", "-m", help="Claude model (e.g. sonnet, opus)")
@click.option("--max-turns", default=10, help="Max agent turns")
@click.option("--stream", is_flag=True, help="Stream response via SSE")
def ask(repo, branch, question, files, diff_range, model, max_turns, stream):
    """Ask Claude a question about code in REPO on BRANCH."""
    token = _get_token()
    mgr = CodespaceManager(token)

    status = console.status(f"Finding codespace for {repo}@{branch}...")
    with status:
        cs_name = mgr.find_or_create(
            repo, branch,
            on_status=lambda msg: status.update(msg),
        )
    console.print(f"Codespace ready: [bold]{cs_name}[/bold]")

    with Tunnel(cs_name) as tunnel:
        with console.status("Waiting for coderev server..."):
            # Poll /health first (unauthenticated), then claim token
            tmp_client = ApiClient(tunnel.local_url, "")
            health = tmp_client.wait_until_ready()

        with console.status("Claiming auth token..."):
            auth_token = claim_auth_token(tunnel.local_url)

        client = ApiClient(tunnel.local_url, auth_token)
        console.print(
            f"Server ready -- repo: {health.get('repo_dir')}, "
            f"branch: {health.get('branch')}, commit: {health.get('commit')}"
        )

        if stream:
            _ask_stream(client, question, list(files), diff_range, model, max_turns)
        else:
            _ask_sync(client, question, list(files), diff_range, model, max_turns)


def _ask_sync(client, question, files, diff_range, model, max_turns):
    with console.status("Claude is thinking..."):
        result = client.ask(
            question=question,
            files=files or None,
            diff_range=diff_range,
            model=model,
            max_turns=max_turns,
        )
    console.print()
    console.print(Markdown(result["answer"]))
    console.print()
    usage = result.get("usage", {})
    duration = result.get("duration_seconds", 0)
    meta_parts = [f"[dim]{duration}s[/dim]"]
    if "cost_usd" in usage:
        meta_parts.append(f"[dim]${usage['cost_usd']:.4f}[/dim]")
    if "num_turns" in usage:
        meta_parts.append(f"[dim]{usage['num_turns']} turns[/dim]")
    console.print(" | ".join(meta_parts))


def _ask_stream(client, question, files, diff_range, model, max_turns):
    for data in client.ask_stream(
        question=question,
        files=files or None,
        diff_range=diff_range,
        model=model,
        max_turns=max_turns,
    ):
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        # Print assistant text content as it arrives
        if event.get("type") == "assistant" and "content" in event:
            for block in event["content"]:
                if block.get("type") == "text":
                    console.print(block["text"], end="")
    console.print()


@cli.command()
@click.argument("repo")
def status(repo):
    """List codespaces for REPO."""
    token = _get_token()
    mgr = CodespaceManager(token)

    codespaces = mgr.list_for_repo(repo)
    if not codespaces:
        console.print(f"No codespaces found for {repo}")
        return

    table = Table(title=f"Codespaces for {repo}")
    table.add_column("Name", style="bold")
    table.add_column("Branch")
    table.add_column("State")
    table.add_column("Machine")

    for cs in codespaces:
        table.add_row(
            cs["name"],
            cs.get("git_status", {}).get("ref", "?"),
            cs.get("state", "?"),
            cs.get("machine", {}).get("display_name", "?"),
        )
    console.print(table)


@cli.command()
@click.argument("repo")
@click.argument("branch", required=False)
def stop(repo, branch):
    """Stop codespace(s) for REPO, optionally filtered by BRANCH."""
    token = _get_token()
    mgr = CodespaceManager(token)

    codespaces = mgr.list_for_repo(repo)
    stopped = 0
    for cs in codespaces:
        cs_branch = cs.get("git_status", {}).get("ref", "")
        if branch and cs_branch != branch:
            continue
        if cs.get("state") == "Available":
            console.print(f"Stopping {cs['name']} ({cs_branch})...")
            mgr.stop(cs["name"])
            stopped += 1

    console.print(f"Stopped {stopped} codespace(s).")


@cli.command()
@click.option("--delete", is_flag=True, help="Also delete stopped codespaces")
def cleanup(delete):
    """Stop idle codespaces. With --delete, also remove stopped ones."""
    token = _get_token()
    mgr = CodespaceManager(token)

    codespaces = mgr.list_all()
    stopped = 0
    deleted = 0

    for cs in codespaces:
        name = cs["name"]
        state = cs.get("state", "")
        if state == "Available":
            console.print(f"Stopping {name}...")
            mgr.stop(name)
            stopped += 1
        elif state in ("Shutdown", "ShuttingDown") and delete:
            console.print(f"Deleting {name}...")
            mgr.delete(name)
            deleted += 1

    console.print(f"Stopped {stopped}, deleted {deleted} codespace(s).")


def _get_token() -> str:
    try:
        return get_github_token()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}", highlight=False)
        sys.exit(1)
