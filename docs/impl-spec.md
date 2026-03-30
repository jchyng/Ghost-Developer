# Ghost Developer 자율 모드 구현 명세서

> 이 문서는 Claude Code 터미널에서 구현을 지시하기 위한 명세서입니다.
> 기존 Ghost Developer 코드 위에 자율 모드를 추가합니다.

---

## 현재 코드베이스

```
Ghost-Developer/
├── server.py            # FastAPI + WebSocket + 태스크 관리
├── orchestrator.py      # Claude Code 호출 루프 + GPT-4o-mini 완료 판정
├── claude_caller.py     # Claude Code headless 호출 + stream-json 파싱
├── db.py                # SQLite (채팅, 메시지, 세션, 스케줄)
├── static/index.html    # xterm.js 기반 웹 UI
├── CLAUDE.md
├── requirements.txt
└── docs/core/           # 기획 문서
```

---

## 변경 범위

### 변경하는 파일
- `orchestrator.py` — 메서드 추가 (기존 코드 수정 최소화)
- `server.py` — 엔드포인트 + 백그라운드 루프 추가
- `db.py` — 테이블 + 함수 추가

### 변경하지 않는 파일
- `claude_caller.py` — 그대로
- `static/index.html` — 1차에서는 UI 변경 없음 (API만 먼저)

---

## 1. DB 변경 (db.py)

### 새 테이블 2개 추가

```sql
-- 자율 모드 설정
CREATE TABLE IF NOT EXISTS auto_config (
    id INTEGER PRIMARY KEY,
    cwd TEXT NOT NULL,
    interval_seconds INTEGER DEFAULT 10800,  -- 기본 3시간
    is_running INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 자율 모드 사이클 기록
CREATE TABLE IF NOT EXISTS auto_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL,
    task TEXT,               -- 선택된 작업 (NULL이면 할 일 없음)
    result TEXT,             -- success / failed / rate_limited / idle
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT
);
```

### 새 함수들

```python
def get_auto_config() -> dict | None
def upsert_auto_config(cwd: str, interval_seconds: int) -> None
def set_auto_running(is_running: bool) -> None
def add_auto_cycle(chat_id: str, cycle_number: int, task: str | None, result: str) -> None
def get_last_cycle_number(chat_id: str) -> int
```

기존 테이블과 함수는 변경하지 않는다.

---

## 2. Orchestrator 변경 (orchestrator.py)

### 추가할 메서드 4개

기존 `Orchestrator` 클래스에 메서드를 추가한다. 기존 `run()`, `_check_done()`, `_git_commit()`은 수정하지 않는다.

#### 2-1. `auto_run()` — 자율 모드 진입점

```
async def auto_run(self, on_event: EventCallback):
```

파이프라인 순서:
1. `_analyze()` 호출 → 프로젝트 상태 스냅샷 획득
2. `_pick_next_task()` 호출 → 다음 작업 결정
3. NONE이면 → idle 이벤트 발행 후 종료
4. TASK이면 → 기존 `_run(task_prompt, on_event)` 호출
5. `_run()` 완료 후 → `_verify()` 호출
6. 검증 실패 시 → `git checkout .` 으로 롤백
7. WORK_LOG.md에 결과 기록
8. cycle_done 이벤트 발행

**중요**: `_run()`을 그대로 재사용한다. `user_message` 자리에 `_pick_next_task()`의 출력을 넣는 것뿐이다.

#### 2-2. `_analyze()` — 프로젝트 스캔

```
async def _analyze(self, cwd: str, session_id: str | None) -> dict:
```

Claude Code에게 스캔 프롬프트를 보내고, JSON 결과를 파싱하여 반환한다.

스캔 프롬프트:
```
프로젝트의 현재 상태를 분석하고 JSON으로 반환하세요.

다음을 순서대로 실행하세요:
1. 테스트 실행 (npm test, pytest 등 프로젝트에 맞는 명령)
2. 린트 실행 (npm run lint 등)
3. 타입 체크 (npx tsc --noEmit 등)
4. grep -rn "TODO\|FIXME" src/ --include="*.ts" --include="*.tsx" --include="*.py"
5. gh issue list --state open --limit 10 --json number,title (gh CLI가 있으면)
6. WORK_LOG.md 읽기 (파일이 있으면)

JSON 형식으로만 응답하세요:
{
  "test": {"pass": true, "failures": []},
  "lint": {"error_count": 0, "files": []},
  "type_errors": {"count": 0, "files": []},
  "todos": [{"file": "", "line": 0, "text": ""}],
  "issues": [{"number": 0, "title": ""}],
  "recent_work": "WORK_LOG 최근 내용 요약"
}
```

**호출 방식**: 기존 `claude_caller.call()`을 사용한다.
- `--max-turns`는 5로 제한 (스캔은 가벼워야 함)
- `--dangerously-skip-permissions` 사용
- `--bare`는 사용하지 않는다 (CLAUDE.md, Hooks 로드 필요)
- `--output-format stream-json` (기존과 동일)

결과에서 JSON을 파싱한다. 파싱 실패 시 빈 스냅샷을 반환한다.

#### 2-3. `_pick_next_task()` — 작업 판단 (GPT-4o-mini)

```
async def _pick_next_task(self, snapshot: dict) -> str | None:
```

기존 `_check_done()`과 동일한 패턴이다. 시스템 프롬프트 + 입력 → 짧은 출력.

시스템 프롬프트:
```
당신은 프로젝트 매니저입니다.
프로젝트 상태 스냅샷과 최근 작업 기록을 보고, 다음에 수행할 작업 1개를 선택하세요.

우선순위:
1. 실패하는 테스트 (최우선)
2. 타입/빌드 에러
3. 린트 에러
4. TODO/FIXME 주석
5. GitHub Issues
6. 테스트 커버리지 개선
7. 코드 개선/리팩토링

규칙:
- WORK_LOG에 최근 완료한 작업은 다시 선택하지 마라.
- 작업은 구체적이고 실행 가능해야 한다.
- 한 번에 하나의 파일 또는 하나의 논리적 단위만.

응답 형식:
TASK: {Claude Code에게 보낼 구체적 작업 지시문}
또는
NONE: {할 일이 없는 이유}
```

유저 메시지:
```
스냅샷:
{snapshot JSON}

최근 작업:
{snapshot["recent_work"]}
```

반환:
- `TASK:` 로 시작하면 → `TASK:` 이후 문자열 반환
- `NONE:` 로 시작하면 → `None` 반환
- API 실패 시 → `None` 반환 (안전 폴백)

#### 2-4. `_verify()` — 검증

```
async def _verify(self, cwd: str, session_id: str | None) -> bool:
```

Claude Code에게 검증 프롬프트를 보낸다.

검증 프롬프트:
```
방금 수정한 내용을 검증하세요:
1. 테스트 전체 실행
2. 린트 체크
3. 타입 체크

모든 항목이 통과하면 "PASS"만 출력하세요.
하나라도 실패하면 "FAIL: {이유}"를 출력하세요.
```

- `--max-turns` 3으로 제한
- 결과에 "PASS"가 포함되면 True, 아니면 False

#### 2-5. `_write_work_log()` — 작업 기록

```
async def _write_work_log(self, cwd: str, task: str, result: str, details: str = ""):
```

Python에서 직접 파일을 쓴다 (Claude Code 호출 불필요, 토큰 절약).

WORK_LOG.md 상단에 추가:
```markdown
## {날짜} {시간} — {result}
- 작업: {task}
- 결과: {result}
- 상세: {details}
```

파일이 없으면 생성한다. `# Work Log\n\n` 헤더를 먼저 쓴다.

---

## 3. Server 변경 (server.py)

### 추가할 엔드포인트 3개

#### POST /auto/start
```json
요청: { "cwd": "/path/to/project", "interval_seconds": 10800 }
응답: { "status": "started", "chat_id": "..." }
```

동작:
1. `auto_config` DB에 설정 저장
2. 해당 cwd에 대한 채팅 생성 (기존 `db.create_chat()` 재사용)
3. `asyncio.create_task(auto_loop(...))` 으로 백그라운드 루프 시작
4. 글로벌 변수 `_auto_task`에 Task 저장

#### POST /auto/stop
```json
응답: { "status": "stopped" }
```

동작:
1. `_auto_task.cancel()`
2. `auto_config`의 `is_running`을 False로

#### GET /auto/status
```json
응답: {
  "is_running": true,
  "cwd": "/path/to/project",
  "current_cycle": 3,
  "last_result": "success",
  "last_task": "auth.test.ts 수정"
}
```

### 백그라운드 루프 함수

```python
async def auto_loop(chat_id: str, cwd: str, interval: int):
    cycle = db.get_last_cycle_number(chat_id)
    
    while True:
        cycle += 1
        orch = Orchestrator(chat_id)
        
        try:
            await orch.auto_run(on_event=_broadcast_auto_event)
        except Exception as e:
            logging.error(f"Auto cycle {cycle} error: {e}")
        
        await asyncio.sleep(interval)
```

`_broadcast_auto_event`는 기존 WebSocket 브로드캐스트 패턴을 재사용한다.
자율 모드 이벤트에 `"mode": "auto"` 필드를 추가하여 수동 모드와 구분한다.

---

## 4. auto_run()의 _run() 호출 시 프롬프트 래핑

`_pick_next_task()`가 반환한 작업 지시문을 기존 `_run()`에 넘길 때, 래핑 프롬프트를 추가한다.

```python
wrapped_prompt = f"""아래 작업을 수행하세요.

## 작업
{task}

## 규칙
- 작업 브랜치를 생성하세요: claude/{{type}}/{{brief-description}}
- 최소한의 변경으로 완료하세요.
- 변경 후 테스트를 실행하여 검증하세요.
- 성공하면 conventional commits 형식으로 커밋하세요.
"""
```

이 `wrapped_prompt`를 `self._run(wrapped_prompt, on_event)`에 넘긴다.

---

## 5. 이벤트 타입 추가

기존 이벤트 (변경 없음):
- `system`, `claude_text`, `tool_use`, `result`, `rate_limit`, `error`, `clarify`, `done`, `cancelled`

자율 모드 추가 이벤트:
- `{"type": "phase", "phase": "analyze|plan|execute|verify|record"}` — 현재 단계
- `{"type": "task_selected", "task": "..."}` — 선택된 작업
- `{"type": "idle", "message": "할 일 없음"}` — NONE 판정
- `{"type": "verify_result", "passed": true|false}` — 검증 결과
- `{"type": "cycle_done", "cycle": 3, "result": "success"}` — 사이클 완료

---

## 6. Claude Code 호출 플래그 정리

자율 모드에서 Claude Code 호출 시 사용하는 플래그:

```
분석: claude -p "{스캔 프롬프트}" --dangerously-skip-permissions --output-format stream-json --max-turns 5
실행: claude -p "{작업 프롬프트}" --dangerously-skip-permissions --output-format stream-json  (기존 _run과 동일)
검증: claude -p "{검증 프롬프트}" --dangerously-skip-permissions --output-format stream-json --max-turns 3
```

**사용하지 않는 플래그:**
- `--bare` — CLAUDE.md, Hooks, Auto Memory 로드가 필요하므로 사용 금지
- `--max-budget-usd` — Pro 구독에서 작동하지 않음
- `--allowedTools` — `--dangerously-skip-permissions` 사용 시 불필요

**세션 관리:**
- 분석/검증은 독립 세션 (일회성)
- 실행은 기존 chat의 session_id를 이어서 사용

---

## 7. 구현 순서

### Phase 1: 코어 (먼저 이것만 하면 동작함)

1. `db.py` — 테이블 2개 + 함수 5개 추가
2. `orchestrator.py` — `_analyze()` 구현
3. `orchestrator.py` — `_pick_next_task()` 구현
4. `orchestrator.py` — `_verify()` 구현
5. `orchestrator.py` — `_write_work_log()` 구현
6. `orchestrator.py` — `auto_run()` 구현 (위 4개를 조합)

### Phase 2: 서버 연동

7. `server.py` — `POST /auto/start` 엔드포인트
8. `server.py` — `POST /auto/stop` 엔드포인트
9. `server.py` — `GET /auto/status` 엔드포인트
10. `server.py` — `auto_loop()` 백그라운드 함수

### Phase 3: 안정화

11. 레이트 리밋 시 자동 대기 후 재개 (기존 rate_limit 이벤트 활용)
12. 연속 실패 3회 시 자율 모드 자동 중지
13. 검증 실패 시 git checkout . 롤백

---

## 8. 테스트 방법

Phase 1 완료 후, Python에서 직접 테스트:

```python
import asyncio
from orchestrator import Orchestrator

async def test():
    orch = Orchestrator("test-chat-id")
    async def on_event(e):
        print(e)
    await orch.auto_run(on_event)

asyncio.run(test())
```

Phase 2 완료 후, API로 테스트:

```bash
# 자율 모드 시작
curl -X POST http://localhost:8000/auto/start \
  -H "Content-Type: application/json" \
  -d '{"cwd": "/path/to/project", "interval_seconds": 60}'

# 상태 확인
curl http://localhost:8000/auto/status

# 중지
curl -X POST http://localhost:8000/auto/stop
```
