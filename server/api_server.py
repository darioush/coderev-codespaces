#!/usr/bin/env python3
"""coderev API server -- wraps Claude Code with read-only tools in a codespace."""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uvicorn

AUTH_TOKEN = os.environ["AUTH_TOKEN"]
REPO_DIR = os.environ["REPO_DIR"]
PORT = 8976

ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Bash(git *)",
    "Bash(gh *)",
    "Bash(cargo *)",
    "Bash(ls *)",
    "Bash(find *)",
    "Bash(wc *)",
    "Bash(head *)",
    "Bash(tail *)",
    "Bash(cat *)",
    "Bash(grep *)",
    "Bash(rg *)",
]

app = FastAPI(title="coderev")
claude_lock = asyncio.Lock()
auth_token_claimed = False


def _verify_auth(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if authorization[7:] != AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


def _git(cmd: str) -> str:
    result = subprocess.run(
        f"git {cmd}",
        shell=True,
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


@app.get("/health")
async def health():
    branch = _git("rev-parse --abbrev-ref HEAD")
    commit = _git("rev-parse --short HEAD")
    claude_version = ""
    try:
        r = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=5
        )
        claude_version = r.stdout.strip()
    except Exception:
        pass
    return {
        "status": "ok",
        "repo_dir": REPO_DIR,
        "branch": branch,
        "commit": commit,
        "claude_version": claude_version,
    }


@app.post("/auth-token")
async def claim_auth_token():
    """One-time endpoint to retrieve the bearer token. Disabled after first use."""
    global auth_token_claimed
    if auth_token_claimed:
        raise HTTPException(status_code=410, detail="Token already claimed")
    auth_token_claimed = True
    return {"token": AUTH_TOKEN}


class SetCredentialsRequest(BaseModel):
    accessToken: str
    refreshToken: str
    expiresAt: int
    scopes: list[str] = Field(default_factory=list)
    subscriptionType: str = ""
    rateLimitTier: str = ""


@app.post("/credentials")
async def set_credentials(req: SetCredentialsRequest, authorization: str | None = Header(None)):
    """Write Claude OAuth credentials to ~/.claude/.credentials.json"""
    _verify_auth(authorization)
    cred_dir = Path.home() / ".claude"
    cred_dir.mkdir(parents=True, exist_ok=True)
    cred_path = cred_dir / ".credentials.json"
    cred_data = {"claudeAiOauth": req.model_dump()}
    cred_path.write_text(json.dumps(cred_data))
    cred_path.chmod(0o600)
    return {"status": "ok"}


class AskRequest(BaseModel):
    question: str
    files: list[str] = Field(default_factory=list)
    diff_range: str | None = None
    model: str | None = None
    max_turns: int = 30
    session_id: str | None = None  # resume a previous conversation


class AskResponse(BaseModel):
    answer: str
    session_id: str | None = None
    usage: dict = Field(default_factory=dict)
    duration_seconds: float


def _build_prompt(req: AskRequest) -> str:
    parts = []
    if req.diff_range:
        parts.append(f"Consider the diff for range `{req.diff_range}`.")
    if req.files:
        parts.append(f"Focus on these files: {', '.join(req.files)}")
    parts.append(req.question)
    return "\n\n".join(parts)


def _build_claude_cmd(req: AskRequest, stream: bool = False) -> list[str]:
    prompt = _build_prompt(req)
    cmd = [
        "claude",
        "-p", prompt,
        "--allowedTools", ",".join(ALLOWED_TOOLS),
        "--max-turns", str(req.max_turns),
        "--output-format", "stream-json" if stream else "json",
    ]
    if req.session_id:
        cmd.extend(["--resume", req.session_id])
    if req.model:
        cmd.extend(["--model", req.model])
    return cmd


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, authorization: str | None = Header(None)):
    _verify_auth(authorization)

    async with claude_lock:
        start = time.monotonic()
        try:
            proc = await asyncio.wait_for(
                _run_claude(req),
                timeout=300,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Claude timed out (120s)")

        elapsed = time.monotonic() - start

    try:
        result = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(
            status_code=502,
            detail=f"Failed to parse Claude output: {proc.stdout[:500]}",
        )

    answer = result.get("result", "")
    if not answer:
        # Fallback for error_max_turns or other subtypes
        answer = result.get("subtype", "No answer returned")
    usage = {}
    if "total_cost_usd" in result:
        usage["cost_usd"] = result["total_cost_usd"]
    if "num_turns" in result:
        usage["num_turns"] = result["num_turns"]
    u = result.get("usage", {})
    if "input_tokens" in u:
        usage["input_tokens"] = u["input_tokens"]
    if "output_tokens" in u:
        usage["output_tokens"] = u["output_tokens"]

    session_id = result.get("session_id")
    return AskResponse(answer=answer, session_id=session_id, usage=usage, duration_seconds=round(elapsed, 2))


def _claude_env() -> dict:
    """Build env for Claude subprocess, stripping ANTHROPIC_API_KEY so it uses OAuth."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    return env


async def _run_claude(req: AskRequest) -> subprocess.CompletedProcess:
    cmd = _build_claude_cmd(req)
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        cwd=REPO_DIR,
        env=_claude_env(),
        timeout=300,
    )
    if proc.returncode != 0 and not proc.stdout:
        raise HTTPException(
            status_code=502, detail=f"Claude failed: {proc.stderr[:500]}"
        )
    return proc


@app.post("/ask/stream")
async def ask_stream(req: AskRequest, authorization: str | None = Header(None)):
    _verify_auth(authorization)

    cmd = _build_claude_cmd(req, stream=True)

    async def generate():
        async with claude_lock:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=REPO_DIR,
                env=_claude_env(),
            )
            try:
                async for line in proc.stdout:
                    decoded = line.decode().strip()
                    if decoded:
                        yield f"data: {decoded}\n\n"
                await asyncio.wait_for(proc.wait(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                yield f"data: {json.dumps({'error': 'timeout'})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
