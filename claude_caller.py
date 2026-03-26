"""
Claude Code headless caller.

Usage:
    async for event in call(prompt, cwd, session_id):
        if event["type"] == "init":
            session_id = event["session_id"]
        elif event["type"] == "text":
            print(event["text"], end="", flush=True)
        elif event["type"] == "result":
            print(event["result"])
        elif event["type"] == "rate_limit":
            retry_after = event["resets_at"]
        elif event["type"] == "error":
            print(event["message"])
"""

import asyncio
import json
from typing import AsyncGenerator


async def call(
    prompt: str,
    cwd: str,
    session_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Run `claude -p <prompt>` and yield structured events.

    Yielded event shapes:
      {"type": "init",       "session_id": str}
      {"type": "text",       "text": str}
      {"type": "tool_use",   "name": str, "input": dict}
      {"type": "result",     "result": str, "session_id": str, "input_tokens": int}
      {"type": "rate_limit", "resets_at": float}
      {"type": "error",      "message": str}
    """
    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]
    if session_id:
        cmd += ["--resume", session_id]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        yield {"type": "error", "message": "claude command not found. Is Claude Code installed?"}
        return

    assert proc.stdout is not None

    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # non-JSON lines (e.g. ansi escape from stderr mixing) — skip
            continue

        yield _normalize(event)

    # Drain stderr for error messages
    stderr_bytes = await proc.stderr.read() if proc.stderr else b""
    await proc.wait()

    if proc.returncode != 0 and stderr_bytes:
        msg = stderr_bytes.decode("utf-8", errors="replace").strip()
        yield {"type": "error", "message": msg}


def _normalize(event: dict) -> dict:
    t = event.get("type", "")

    if t == "system" and event.get("subtype") == "init":
        return {"type": "init", "session_id": event.get("session_id", "")}

    if t == "assistant":
        # Extract text content from the message
        message = event.get("message", {})
        texts = [
            c.get("text", "")
            for c in message.get("content", [])
            if c.get("type") == "text"
        ]
        tool_uses = [
            {"name": c.get("name", ""), "input": c.get("input", {})}
            for c in message.get("content", [])
            if c.get("type") == "tool_use"
        ]
        if texts:
            return {"type": "text", "text": "".join(texts)}
        if tool_uses:
            return {"type": "tool_use", "tools": tool_uses}
        return {"type": "text", "text": ""}

    if t == "result":
        usage = event.get("usage", {})
        return {
            "type": "result",
            "result": event.get("result", ""),
            "session_id": event.get("session_id", ""),
            "input_tokens": usage.get("input_tokens", 0),
            "is_error": event.get("is_error", False),
        }

    if t == "rate_limit_event":
        info = event.get("rate_limit_info", {})
        return {
            "type": "rate_limit",
            "resets_at": float(info.get("resetsAt", 0)),
            "allowed": info.get("status") == "allowed",
        }

    # Pass through anything else with its original type
    return {"type": t, "raw": event}
