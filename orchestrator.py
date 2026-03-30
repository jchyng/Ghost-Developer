"""
Orchestrator: gpt-5.4-mini가 Claude Code 호출 전략을 결정하는 루프.

- 사용자 메시지 수신 → Claude Code 호출 → 완료 판단 → 반복
- 완료 판단 불가 시 사용자에게 확인 요청
- Rate limit 감지 시 DB에 스케줄 저장 후 중단
- 컨텍스트 80% 도달 시 /compact 자동 호출
"""

import asyncio
import json
import os
import re
import subprocess
from datetime import datetime
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
        # _running 등록은 호출자(server.py)가 이미 선점했으므로 여기선 정리만 담당
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

    # ── 자율 모드 메서드 ───────────────────────────────────────────────────

    async def _analyze(self, cwd: str, session_id: str | None) -> dict:
        """프로젝트 상태를 스캔하고 JSON 스냅샷을 반환한다."""
        prompt = (
            "프로젝트의 현재 상태를 분석하고 JSON으로 반환하세요. "
            "5턴 이내로 완료해.\n"
            "다음을 순서대로 실행하세요:\n"
            "1. 테스트 실행\n"
            "2. 린트 실행\n"
            "3. 타입 체크\n"
            "4. grep -rn 'TODO\\|FIXME' src/\n"
            "5. gh issue list --state open --limit 10 --json number,title\n"
            "6. WORK_LOG.md 읽기\n\n"
            'JSON 형식: {"test": {"pass": bool, "failures": []}, '
            '"lint": {"error_count": 0, "files": []}, '
            '"type_errors": {"count": 0, "files": []}, '
            '"todos": [{"file": "", "line": 0, "text": ""}], '
            '"issues": [{"number": 0, "title": ""}], '
            '"recent_work": ""}'
        )

        full_text = ""
        async for event in claude_caller.call(prompt, cwd, session_id):
            if event["type"] == "text":
                full_text += event["text"]
            elif event["type"] == "result":
                full_text += event.get("result", "")
            elif event["type"] == "rate_limit" and not event.get("allowed", True):
                return {}
            elif event["type"] == "error":
                return {}

        # 마크다운 코드블록 또는 raw JSON 추출
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", full_text, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            match = re.search(r"\{.*\}", full_text, re.DOTALL)
            json_str = match.group(0) if match else None

        if json_str:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                return {}
        return {}

    async def _pick_next_task(self, snapshot: dict) -> str | None:
        """GPT-4o-mini에게 다음 작업 1개를 판단 요청한다."""
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None

        model = os.getenv("ORCHESTRATOR_MODEL", "gpt-5.4-mini")
        client = AsyncOpenAI(api_key=api_key)
        recent_work = snapshot.get("recent_work", "없음")

        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "당신은 프로젝트 매니저입니다. 프로젝트 상태 스냅샷과 최근 작업 기록을 보고, "
                            "다음에 수행할 작업 1개를 선택하세요.\n\n"
                            "우선순위:\n"
                            "1. 실패하는 테스트\n"
                            "2. 타입/빌드 에러\n"
                            "3. 린트 에러\n"
                            "4. TODO/FIXME\n"
                            "5. GitHub Issues\n"
                            "6. 테스트 커버리지 개선\n"
                            "7. 코드 개선\n\n"
                            "규칙:\n"
                            "- WORK_LOG에 최근 완료한 작업은 다시 선택하지 마라\n"
                            "- 작업은 구체적이고 실행 가능해야 한다\n"
                            "- 한 번에 하나의 논리적 단위만\n\n"
                            "응답: TASK: {작업 지시문} 또는 NONE: {이유}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"프로젝트 상태 스냅샷:\n"
                            f"{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n\n"
                            f"최근 작업 기록:\n{recent_work}"
                        ),
                    },
                ],
                max_tokens=200,
                temperature=0,
            )
            answer = resp.choices[0].message.content.strip()
            if answer.startswith("TASK:"):
                return answer[len("TASK:"):].strip()
            return None
        except Exception:
            return None

    async def _verify(self, cwd: str, session_id: str | None) -> bool:
        """Claude Code에게 변경 사항 검증을 요청하고 PASS/FAIL을 반환한다."""
        prompt = (
            "방금 수정한 내용을 검증하세요. "
            "테스트, 린트, 타입 체크를 실행하세요. "
            "통과하면 PASS, 실패하면 FAIL: {이유}를 출력하세요."
        )

        full_text = ""
        async for event in claude_caller.call(prompt, cwd, session_id):
            if event["type"] == "text":
                full_text += event["text"]
            elif event["type"] == "result":
                full_text += event.get("result", "")
            elif event["type"] == "rate_limit" and not event.get("allowed", True):
                return False
            elif event["type"] == "error":
                return False

        return "PASS" in full_text

    def _write_work_log(self, cwd: str, task: str, result: str, details: str = ""):
        """WORK_LOG.md 파일 상단에 작업 기록을 추가한다. Claude Code 호출 없이 직접 파일 I/O."""
        log_path = os.path.join(cwd, "WORK_LOG.md")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = (
            f"## {timestamp} — {result}\n"
            f"- 작업: {task}\n"
            f"- 결과: {result}\n"
            f"- 상세: {details}\n\n"
        )

        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                existing = f.read()
            if existing.startswith("# Work Log") and "\n\n" in existing:
                header, rest = existing.split("\n\n", 1)
                content = f"{header}\n\n{entry}{rest}"
            else:
                content = entry + existing
        else:
            content = f"# Work Log\n\n{entry}"

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(content)

    async def auto_run(self, on_event: EventCallback):
        """자율 모드 진입점: 분석 → 계획 → 실행 → 검증 한 사이클을 수행한다."""
        chat = db.get_chat(self.chat_id)
        if not chat:
            await on_event({"type": "error", "message": "chat not found"})
            return

        cwd = chat["cwd"]
        session_id = chat["session_id"]

        # 1. Analyze
        await on_event({"type": "phase", "phase": "analyze"})
        snapshot = await self._analyze(cwd, session_id)

        # 2. Plan
        await on_event({"type": "phase", "phase": "plan"})
        task = await self._pick_next_task(snapshot)

        if task is None:
            self._write_work_log(cwd, "idle", "idle", "할 작업 없음")
            await on_event({"type": "idle"})
            return

        await on_event({"type": "task_selected", "task": task})

        # 3. Execute
        await on_event({"type": "phase", "phase": "execute"})
        wrapped_prompt = (
            "아래 작업을 수행하세요.\n\n"
            f"## 작업\n{task}\n\n"
            "## 규칙\n"
            "- 작업 브랜치 생성: claude/{type}/{description}\n"
            "- 최소한의 변경\n"
            "- 변경 후 테스트 실행\n"
            "- conventional commits 형식으로 커밋"
        )
        await self._run(wrapped_prompt, on_event)

        # 4. Verify — _run이 session_id를 DB에 저장했을 수 있으므로 재조회
        await on_event({"type": "phase", "phase": "verify"})
        updated_chat = db.get_chat(self.chat_id)
        current_session_id = updated_chat["session_id"] if updated_chat else session_id
        passed = await self._verify(cwd, current_session_id)

        if not passed:
            loop = asyncio.get_event_loop()

            def _rollback():
                subprocess.run(
                    ["git", "checkout", "."],
                    cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                subprocess.run(
                    ["git", "clean", "-fd"],
                    cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                )

            await loop.run_in_executor(None, _rollback)
            self._write_work_log(cwd, task, "failed", "검증 실패로 롤백됨")
            result_str = "failed"
        else:
            self._write_work_log(cwd, task, "success")
            result_str = "success"

        cycle_number = db.get_last_cycle_number(self.chat_id) + 1
        db.add_auto_cycle(self.chat_id, cycle_number, task, result_str)

        await on_event({"type": "cycle_done", "result": result_str})


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

    model = os.getenv("ORCHESTRATOR_MODEL", "gpt-5.4-mini")
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
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    for args in (["git", "add", "."], ["git", "commit", "-m", message]):
        await loop.run_in_executor(None, _run, args)
