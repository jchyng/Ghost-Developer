import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from openai import AsyncOpenAI

import db
import orchestrator as orc

load_dotenv()

app = FastAPI()
os.makedirs("static", exist_ok=True)
db.init_db()

sessions: dict = {}   # session_id -> PtyProcess  (수동 터미널)
tasks: list = []       # 모든 작업 목록
cwd_locks: dict = {}  # cwd -> asyncio.Lock (동일 경로 직렬화)
_auto_task: asyncio.Task | None = None


# ── PTY 생성 (플랫폼 분기) ────────────────────────────────────────────────

def spawn_pty(cmd, cwd=None, dimensions=(24, 80)):
    if sys.platform == "win32":
        from winpty import PtyProcess
        return PtyProcess.spawn(cmd, cwd=cwd, dimensions=dimensions)
    else:
        import ptyprocess
        if isinstance(cmd, str):
            cmd = ["bash", "-c", cmd]
        return ptyprocess.PtyProcessUnicode.spawn(cmd, cwd=cwd, dimensions=dimensions)


# ── Git Auto-commit ───────────────────────────────────────────────────────

async def git_commit(cwd: str, message: str, output_buf: bytearray | None = None):
    # asyncio.create_subprocess_exec + PIPE 조합이 Windows에서 불안정하므로
    # subprocess.run을 스레드 executor에서 실행한다.
    loop = asyncio.get_event_loop()

    def _run(args):
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True)

    for args in (["git", "add", "."], ["git", "commit", "-m", message]):
        result = await loop.run_in_executor(None, _run, args)
        if output_buf is not None and result.stderr:
            line = f"\r\n\x1b[90m[git] {result.stderr.strip()}\x1b[0m\r\n"
            output_buf.extend(line.encode())


# ── 태스크 직렬화 (내부 필드 제외) ───────────────────────────────────────

def task_to_dict(t: dict) -> dict:
    return {k: v for k, v in t.items() if k not in ("output", "output_event", "proc")}


# ── 태스크 실행기 ─────────────────────────────────────────────────────────

async def run_task(task: dict):
    cwd = task["cwd"]
    cwd_locks.setdefault(cwd, asyncio.Lock())

    async with cwd_locks[cwd]:
        task["status"] = "running"
        task["output"] = bytearray()
        task["output_event"] = asyncio.Event()

        try:
            await git_commit(cwd, f"Auto-commit: Before {task['prompt'][:50]}", task["output"])
        except Exception as e:
            task["output"].extend(f"\r\n\x1b[90m[git error] {e}\x1b[0m\r\n".encode())

        safe_prompt = task["prompt"].replace('"', "'")
        if sys.platform == "win32":
            # PowerShell -Command 모드에서는 Node.js(claude) 출력이 winpty에 캡처되지 않음.
            # 인터랙티브 PowerShell을 스폰한 뒤 stdin으로 명령을 주입하면
            # Shell 터미널과 동일한 방식으로 동작해 출력이 캡처된다.
            safe_prompt_ps = safe_prompt.replace("'", "''")
            cmd = "powershell.exe -NoProfile -NoLogo"
        else:
            cmd = f'claude --dangerously-skip-permissions -p "{safe_prompt}"'

        try:
            proc = spawn_pty(cmd, cwd=cwd)
        except Exception as e:
            task["status"] = "error"
            task["output"].extend(f"\r\n[프로세스 실행 실패: {e}]\r\n".encode())
            task["output_event"].set()
            return

        task["proc"] = proc

        if sys.platform == "win32":
            # PS 초기화 대기 후 명령 주입; 완료 후 PowerShell이 자동 종료되도록 exit 추가
            await asyncio.sleep(1.0)
            proc.write(f"claude --dangerously-skip-permissions -p '{safe_prompt_ps}'; exit\r\n")

        loop = asyncio.get_event_loop()

        # PTY 출력을 output 버퍼에 누적 (WS가 구독)
        async def drain():
            empty_streak = 0
            while True:
                try:
                    data = await loop.run_in_executor(None, proc.read, 4096)
                    if data:
                        empty_streak = 0
                        encoded = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
                        task["output"].extend(encoded)
                        task["output_event"].set()
                    else:
                        # 빈 읽기: 프로세스가 종료됐으면 루프 탈출
                        empty_streak += 1
                        if not proc.isalive() or empty_streak > 5:
                            break
                        await asyncio.sleep(0.05)
                except Exception:
                    break
            task["output_event"].set()

        drain_task = asyncio.create_task(drain())

        # 프로세스 종료 대기
        while proc.isalive():
            await asyncio.sleep(0.3)

        # proc 종료 후 drain()이 남은 PTY 출력을 다 읽을 때까지 대기 (최대 5초)
        # proc.isalive()가 False가 되어도 winpty 버퍼에 아직 읽히지 않은
        # 출력(e.g. claude 응답)이 남아 있을 수 있으므로 먼저 flush한다.
        try:
            await asyncio.wait_for(asyncio.shield(drain_task), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        # 외부에서 cancel된 경우 status가 이미 "cancelled"
        if task["status"] != "cancelled":
            task["status"] = "done"
        task["output_event"].set()

        try:
            await git_commit(cwd, f"Auto-commit: After {task['prompt'][:50]}", task["output"])
        except Exception as e:
            task["output"].extend(f"\r\n\x1b[90m[git error] {e}\x1b[0m\r\n".encode())


# ── 파일시스템 브라우저 ────────────────────────────────────────────────────

@app.get("/api/fs")
async def browse_fs(path: str = ""):
    if not path:
        path = os.path.expanduser("~")
    try:
        abs_path = os.path.abspath(path)
        if not os.path.isdir(abs_path):
            return {"path": abs_path, "entries": [], "error": "not a directory"}
        entries = []
        for name in sorted(os.listdir(abs_path)):
            if name.startswith("."):
                continue
            full = os.path.join(abs_path, name)
            if os.path.isdir(full):
                entries.append({"name": name, "type": "dir"})
        return {"path": abs_path, "entries": entries}
    except PermissionError:
        return {"path": path, "entries": [], "error": "permission denied"}
    except Exception as e:
        return {"path": path, "entries": [], "error": str(e)}


# ── REST API ──────────────────────────────────────────────────────────────

@app.post("/tasks")
async def create_task(body: dict):
    task_id = str(uuid.uuid4())[:8]
    task = {
        "id": task_id,
        "cwd": body["cwd"],
        "prompt": body["prompt"],
        "status": "pending",
        "session_id": f"task-{task_id}",
        "output": bytearray(),
        "output_event": None,
        "proc": None,
    }
    tasks.append(task)
    asyncio.create_task(run_task(task))
    return task_to_dict(task)


@app.get("/tasks")
async def list_tasks():
    return [task_to_dict(t) for t in tasks]


@app.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        return {"error": "not found"}
    if task["status"] == "running":
        task["status"] = "cancelled"
        proc = task.get("proc")
        if proc and proc.isalive():
            proc.close()
    return task_to_dict(task)


# ── WebSocket ─────────────────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def terminal_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    loop = asyncio.get_event_loop()

    # ── 태스크 터미널 (읽기 전용 라이브 뷰) ──────────────────────────────
    if session_id.startswith("task-"):
        task = next((t for t in tasks if t["session_id"] == session_id), None)
        if not task:
            await websocket.send_bytes(b"\r\n[task not found]\r\n")
            await websocket.close()
            return

        # run_task가 output_event를 초기화할 때까지 대기
        # 기존 출력이 없을 때만 대기 메시지 출력 (재연결 시 중복 방지)
        if task["output_event"] is None:
            await websocket.send_bytes(b"\r\n\x1b[90m[\xec\x9e\xa0\xec\x8b\x9c \xeb\x8c\x80\xea\xb8\xb0 \xec\xa4\x91...]\x1b[0m\r\n")
            while task["output_event"] is None:
                await asyncio.sleep(0.1)

        # 기존 누적 출력 재생
        pos = len(task["output"])
        if pos:
            await websocket.send_bytes(bytes(task["output"]))

        # 라이브 스트리밍
        while task["status"] in ("pending", "running"):
            try:
                await asyncio.wait_for(task["output_event"].wait(), timeout=1.0)
                task["output_event"].clear()
            except asyncio.TimeoutError:
                pass
            chunk = bytes(task["output"][pos:])
            if chunk:
                try:
                    await websocket.send_bytes(chunk)
                    pos += len(chunk)
                except Exception:
                    return

        # 마지막 잔여 출력 flush
        tail = bytes(task["output"][pos:])
        if tail:
            try:
                await websocket.send_bytes(tail)
            except Exception:
                pass
        return

    # ── 수동 터미널 (powershell / bash) ──────────────────────────────────
    if session_id not in sessions or not sessions[session_id].isalive():
        cmd = "powershell.exe" if sys.platform == "win32" else "bash"
        sessions[session_id] = spawn_pty(cmd)

    proc = sessions[session_id]

    async def pty_to_ws():
        try:
            while True:
                data = await loop.run_in_executor(None, proc.read, 4096)
                if data:
                    if isinstance(data, str):
                        data = data.encode("utf-8", errors="replace")
                    await websocket.send_bytes(data)
        except Exception:
            pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    async def ws_to_pty():
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                raw: bytes = msg.get("bytes") or (msg.get("text") or "").encode()
                if not raw:
                    continue
                if raw[0:1] == b"{":
                    try:
                        obj = json.loads(raw)
                        if obj.get("type") == "resize":
                            proc.setwinsize(int(obj["rows"]), int(obj["cols"]))
                            continue
                    except Exception:
                        pass
                proc.write(raw.decode("utf-8", errors="replace"))
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            pass  # PTY는 WS 단절 시 유지; DELETE /api/shells/{id} 로만 종료

    await asyncio.gather(pty_to_ws(), ws_to_pty(), return_exceptions=True)


@app.delete("/api/shells/{session_id}")
async def close_shell(session_id: str):
    proc = sessions.pop(session_id, None)
    if proc and proc.isalive():
        proc.close()
    return {"ok": True}


@app.post("/api/tasks")
async def create_task_api(body: dict):
    return await create_task(body)


@app.get("/api/tasks")
async def list_tasks_api():
    return await list_tasks()


@app.delete("/api/tasks/{task_id}")
async def cancel_task_api(task_id: str):
    return await cancel_task(task_id)


# ── v2 Chat API ───────────────────────────────────────────────────────────

@app.post("/api/chats")
async def create_chat(body: dict):
    cwd = body.get("cwd", "")
    title = body.get("title") or cwd or "New Chat"
    return db.create_chat(cwd=cwd, title=title)


@app.get("/api/chats")
async def list_chats():
    return db.list_chats()


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str):
    chat = db.get_chat(chat_id)
    if not chat:
        return {"error": "not found"}, 404
    return chat


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    db.delete_chat(chat_id)
    return {"ok": True}


@app.patch("/api/chats/{chat_id}")
async def rename_chat(chat_id: str, body: dict):
    title = (body.get("title") or "").strip()
    if not title:
        return {"error": "title required"}
    chat = db.update_chat_title(chat_id, title)
    if not chat:
        return {"error": "not found"}
    await _broadcast(chat_id, {"type": "title_update", "chat_id": chat_id, "title": title})
    return chat


@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: str):
    return db.list_messages(chat_id)


@app.post("/api/chats/{chat_id}/send")
async def send_message(chat_id: str, body: dict):
    """사용자 메시지를 받아 Orchestrator를 백그라운드로 실행한다."""
    content = body.get("content", "").strip()
    if not content:
        return {"error": "empty message"}

    chat = db.get_chat(chat_id)
    if not chat:
        return {"error": "chat not found"}

    # 이미 실행 중인 Orchestrator가 있으면 거절
    if orc.get_running(chat_id):
        return {"error": "already running"}

    # _running을 즉시 선점해 race condition 방지 (task 시작 전에 두 번째 요청 차단)
    sentinel = orc.Orchestrator(chat_id)
    orc._running[chat_id] = sentinel

    # 첫 메시지 여부 확인 (orchestrator가 저장하기 전에 체크)
    is_first = len(db.list_messages(chat_id)) == 0

    # Orchestrator는 백그라운드 태스크로 실행
    asyncio.create_task(_run_orchestrator(chat_id, content, sentinel))
    if is_first:
        asyncio.create_task(_generate_title(chat_id, content))
    return {"ok": True}


@app.delete("/api/chats/{chat_id}/cancel")
async def cancel_chat(chat_id: str):
    """실행 중인 Orchestrator를 중단한다."""
    instance = orc.get_running(chat_id)
    if instance:
        instance.cancel()
        return {"ok": True}
    return {"error": "not running"}


# ── Chat WebSocket (이벤트 스트림) ────────────────────────────────────────

# chat_id -> set of WebSocket subscribers
_chat_subs: dict[str, set[WebSocket]] = {}


@app.websocket("/ws/chats/{chat_id}")
async def chat_ws(websocket: WebSocket, chat_id: str):
    await websocket.accept()
    _chat_subs.setdefault(chat_id, set()).add(websocket)
    try:
        # 연결 즉시 기존 메시지 히스토리 전송
        messages = db.list_messages(chat_id)
        await websocket.send_text(json.dumps({"type": "history", "messages": messages}))

        # 클라이언트가 연결을 끊을 때까지 대기
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _chat_subs.get(chat_id, set()).discard(websocket)


async def _broadcast(chat_id: str, event: dict):
    """해당 chat의 모든 WebSocket 구독자에게 이벤트를 전송한다."""
    dead = set()
    for ws in _chat_subs.get(chat_id, set()):
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            dead.add(ws)
    _chat_subs.get(chat_id, set()).difference_update(dead)


async def _generate_title(chat_id: str, first_message: str):
    """첫 메시지 내용을 바탕으로 GPT가 채팅 제목을 자동 생성한다."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return
    model = os.getenv("ORCHESTRATOR_MODEL", "gpt-4o-mini")
    client = AsyncOpenAI(api_key=api_key)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content":
                    "Generate a concise chat title of 4-6 words that captures the task. "
                    "Return only the title, no punctuation, no quotes."},
                {"role": "user", "content": first_message},
            ],
            max_tokens=20,
            temperature=0.3,
        )
        title = resp.choices[0].message.content.strip()
        if title:
            db.update_chat_title(chat_id, title)
            await _broadcast(chat_id, {"type": "title_update", "chat_id": chat_id, "title": title})
    except Exception:
        pass  # 실패해도 조용히 — 기존 title(cwd) 유지


async def _run_orchestrator(chat_id: str, user_message: str, instance: orc.Orchestrator):
    async def on_event(event: dict):
        await _broadcast(chat_id, event)

    await instance.run(user_message, on_event)


# ── 자율 모드 ─────────────────────────────────────────────────────────────

async def auto_loop(chat_id: str, cwd: str, interval: int):
    consecutive_failures = 0

    while True:
        rate_limit_at = 0.0
        cycle_result = None

        async def on_event(event: dict):
            nonlocal rate_limit_at, cycle_result
            logging.info("[AUTO:%s] %s", chat_id, event)
            if event["type"] == "rate_limit":
                rate_limit_at = event.get("resets_at", 0.0)
            elif event["type"] == "cycle_done":
                cycle_result = event.get("result")
            await _broadcast(chat_id, event)

        orch = orc.Orchestrator(chat_id)
        orc._running[chat_id] = orch
        try:
            await orch.auto_run(on_event=on_event)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error("Auto cycle error (chat=%s): %s", chat_id, e)
        finally:
            orc._running.pop(chat_id, None)

        # 1. 레이트 리밋: resets_at까지 대기 후 동일 사이클 재시도 (interval 스킵)
        if rate_limit_at > 0:
            wait = rate_limit_at - time.time()
            if wait > 0:
                logging.info("[AUTO] Rate limited. Sleeping %.0fs until reset.", wait)
                try:
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    break
            continue

        # 2. 연속 실패 카운트 — 3회 연속 failed이면 자동 중지
        if cycle_result == "failed":
            consecutive_failures += 1
        else:
            consecutive_failures = 0

        if consecutive_failures >= 3:
            logging.warning("연속 3회 실패로 자율 모드 중지됨")
            db.set_auto_running(False)
            _auto_task.cancel()
            break

        # 3. 정상 인터벌 대기
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break


@app.post("/auto/start")
async def auto_start(body: dict):
    global _auto_task
    if _auto_task and not _auto_task.done():
        return JSONResponse({"error": "already running"}, status_code=400)

    cwd = body.get("cwd", "")
    interval = int(body.get("interval_seconds", 10800))

    db.upsert_auto_config(cwd, interval)

    chat = db.create_chat(cwd, f"Auto Mode — {cwd}")
    chat_id = chat["id"]

    _auto_task = asyncio.create_task(auto_loop(chat_id, cwd, interval))
    db.set_auto_running(True)

    return {"status": "started", "chat_id": chat_id}


@app.post("/auto/stop")
async def auto_stop():
    global _auto_task
    if _auto_task and not _auto_task.done():
        _auto_task.cancel()
    db.set_auto_running(False)
    return {"status": "stopped"}


@app.get("/auto/status")
async def auto_status():
    config = db.get_auto_config()
    if not config:
        return {
            "is_running": False,
            "cwd": None,
            "interval_seconds": 10800,
            "last_cycle": None,
        }

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT cycle_number, task, result, finished_at "
            "FROM auto_cycles ORDER BY id DESC LIMIT 1"
        ).fetchone()
    last_cycle = dict(row) if row else None

    return {
        "is_running": bool(config["is_running"]),
        "cwd": config["cwd"],
        "interval_seconds": config["interval_seconds"],
        "last_cycle": last_cycle,
    }


@app.on_event("startup")
async def _startup():
    """서버 재시작 시 is_running=True인 자율 모드 설정이 있으면 자동 재개한다."""
    global _auto_task
    config = db.get_auto_config()
    if config and config["is_running"]:
        chat = db.create_chat(config["cwd"], f"Auto Mode — {config['cwd']}")
        _auto_task = asyncio.create_task(
            auto_loop(chat["id"], config["cwd"], config["interval_seconds"])
        )
        logging.info("Auto mode resumed on startup (cwd=%s)", config["cwd"])


app.mount("/", StaticFiles(directory="static", html=True), name="static")
