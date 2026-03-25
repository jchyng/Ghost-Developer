import asyncio
import json
import os
import sys
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

app = FastAPI()
os.makedirs("static", exist_ok=True)

sessions: dict = {}   # session_id -> PtyProcess  (수동 터미널)
tasks: list = []       # 모든 작업 목록
cwd_locks: dict = {}  # cwd -> asyncio.Lock (동일 경로 직렬화)


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

async def git_commit(cwd: str, message: str):
    for args in (["git", "add", "."], ["git", "commit", "-m", message]):
        p = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await p.wait()


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

        await git_commit(cwd, f"Auto-commit: Before {task['prompt'][:50]}")

        safe_prompt = task["prompt"].replace('"', "'")
        cmd = f'claude --dangerously-skip-permissions -p "{safe_prompt}"'

        try:
            proc = spawn_pty(cmd, cwd=cwd)
        except Exception as e:
            task["status"] = "error"
            task["output"].extend(f"\r\n[프로세스 실행 실패: {e}]\r\n".encode())
            task["output_event"].set()
            return

        task["proc"] = proc

        loop = asyncio.get_event_loop()

        # PTY 출력을 output 버퍼에 누적 (WS가 구독)
        async def drain():
            while True:
                try:
                    data = await loop.run_in_executor(None, proc.read, 4096)
                    if data:
                        encoded = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
                        task["output"].extend(encoded)
                        task["output_event"].set()
                except Exception:
                    break
            task["output_event"].set()

        asyncio.create_task(drain())

        # 프로세스 종료 대기
        while proc.isalive():
            await asyncio.sleep(0.3)

        # 외부에서 cancel된 경우 status가 이미 "cancelled"
        if task["status"] != "cancelled":
            task["status"] = "done"
        task["output_event"].set()

        await git_commit(cwd, f"Auto-commit: After {task['prompt'][:50]}")


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
            proc.close()
            sessions.pop(session_id, None)

    await asyncio.gather(pty_to_ws(), ws_to_pty(), return_exceptions=True)


app.mount("/", StaticFiles(directory="static", html=True), name="static")
