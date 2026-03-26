"""
Orchestrator: gpt-4o-mini가 Claude Code 호출 전략을 결정하는 루프.

- 사용자 메시지 수신 → Claude Code 호출 → 완료 판단 → 반복
- 완료 판단 불가 시 사용자에게 확인 요청
- Rate limit 감지 시 DB에 스케줄 저장 후 중단
- 컨텍스트 80% 도달 시 /compact 자동 호출
"""

import asyncio
import os
from typing import Awaitable, Callable

from dotenv import load_dotenv
from openai import AsyncOpenAI

import claude_caller
import db

load_dotenv()

CONTEXT_WINDOW = 200_000
COMPACT_THRESHOLD = 0.80
MAX_TURNS = 10

EventCallback = Callable[[dict], Awaitable[None]]

# chat_id -> Orchestrator (실행 중인 인스턴스 추적)
_running: dict[str, "Orchestrator"] = {}


def get_running(chat_id: str) -> "Orchestrator | None":
    return _running.get(chat_id)


class Orchestrator:
    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def run(self, user_message: str, on_event: EventCallback):
        _running[self.chat_id] = self
        try:
            await self._run(user_message, on_event)
        finally:
            _running.pop(self.chat_id, None)

    async def _run(self, user_message: str, on_event: EventCallback):
        chat = db.get_chat(self.chat_id)
        if not chat:
            await on_event({"type": "error", "message": "chat not found"})
            return

        cwd = chat["cwd"]
        session_id = chat["session_id"]  # None이면 첫 호출

        # 사용자 메시지 저장
        db.add_message(self.chat_id, "user", user_message)

        # git Before
        await on_event({"type": "system", "text": f"[GIT] Auto-commit: Before {user_message[:50]}"})
        await _git_commit(cwd, f"Auto-commit: Before {user_message[:50]}")

        prompt = user_message
        final_result = ""
        input_tokens = 0

        for turn in range(MAX_TURNS):
            if self._cancelled:
                await on_event({"type": "cancelled"})
                return

            await on_event({"type": "system", "text": f"[TURN {turn + 1}] Calling Claude Code..."})

            # Claude Code 호출
            async for event in claude_caller.call(prompt, cwd, session_id):
                if self._cancelled:
                    await on_event({"type": "cancelled"})
                    return

                etype = event["type"]

                if etype == "init":
                    new_sid = event["session_id"]
                    if not session_id and new_sid:
                        session_id = new_sid
                        db.update_chat_session_id(self.chat_id, session_id)

                elif etype == "text":
                    await on_event({"type": "claude_text", "text": event["text"]})

                elif etype == "tool_use":
                    for tool in event.get("tools", []):
                        await on_event({
                            "type": "tool_use",
                            "name": tool["name"],
                            "input": tool.get("input", {}),
                        })

                elif etype == "result":
                    final_result = event["result"]
                    input_tokens = event["input_tokens"]
                    if not session_id and event.get("session_id"):
                        session_id = event["session_id"]
                        db.update_chat_session_id(self.chat_id, session_id)

                elif etype == "rate_limit" and not event["allowed"]:
                    resets_at = event["resets_at"]
                    db.upsert_schedule(self.chat_id, resets_at)
                    await on_event({"type": "rate_limit", "resets_at": resets_at})
                    return

                elif etype == "error":
                    await on_event({"type": "error", "message": event["message"]})
                    return

            # 컨텍스트 80% 초과 시 compact
            if input_tokens > CONTEXT_WINDOW * COMPACT_THRESHOLD:
                await on_event({"type": "system", "text": "[CONTEXT] Threshold reached — compacting..."})
                async for _ in claude_caller.call("/compact", cwd, session_id):
                    pass

            # 완료 판단
            done, uncertain = await _check_done(user_message, final_result)

            if uncertain:
                await on_event({"type": "clarify", "message": final_result})
                # 사용자 응답을 기다리지 않고 완료로 처리 (사용자가 다음 메시지로 이어서 지시)
                break

            if done:
                break

            # 아직 완료되지 않음 → 다음 턴
            prompt = "이전 작업을 계속 완료해주세요."

        # 최종 메시지 저장
        db.add_message(self.chat_id, "assistant", final_result)

        # git After
        await on_event({"type": "system", "text": f"[GIT] Auto-commit: After {user_message[:50]}"})
        await _git_commit(cwd, f"Auto-commit: After {user_message[:50]}")

        await on_event({"type": "done", "result": final_result})


# ── 완료 판단 ──────────────────────────────────────────────────────────────

async def _check_done(original_prompt: str, claude_response: str) -> tuple[bool, bool]:
    """
    Returns (done, uncertain).
    done=True → 작업 완료
    uncertain=True → 판단 불가, 사용자에게 확인 필요
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        # API 키 없으면 단일 턴으로 완료 처리
        return True, False

    model = os.getenv("ORCHESTRATOR_MODEL", "gpt-4o-mini")
    client = AsyncOpenAI(api_key=api_key)

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a task completion detector. "
                        "Given a user's original task and Claude's response, reply with exactly one word:\n"
                        "  DONE     — the task is fully completed\n"
                        "  CONTINUE — the task is not yet done, more work is needed\n"
                        "  UNCLEAR  — you cannot determine completion"
                    ),
                },
                {
                    "role": "user",
                    "content": f"Task: {original_prompt}\n\nClaude's response:\n{claude_response}",
                },
            ],
            max_tokens=5,
            temperature=0,
        )
        answer = resp.choices[0].message.content.strip().upper()
        if "DONE" in answer:
            return True, False
        if "UNCLEAR" in answer:
            return False, True
        return False, False  # CONTINUE
    except Exception:
        # API 실패 시 단일 턴 완료로 폴백
        return True, False


# ── Git helper ─────────────────────────────────────────────────────────────

async def _git_commit(cwd: str, message: str):
    loop = asyncio.get_event_loop()

    def _run(args):
        import subprocess
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True)

    for args in (["git", "add", "."], ["git", "commit", "-m", message]):
        await loop.run_in_executor(None, _run, args)
