# Kinetic AI — 구현 현황 문서

> 최종 업데이트: 2026-03-26

---

## 1. 프로젝트 개요

로컬에서 실행하는 **AI 에이전트 태스크 러너**. 개발자가 브라우저 UI에서 작업 지시를 입력하면 백엔드가 `claude CLI`를 PTY로 실행하고 실시간으로 터미널 출력을 스트리밍한다. 동시에 인터랙티브 셸(PowerShell/bash)도 멀티 탭으로 운용 가능하다.

---

## 2. 기술 스택

| 계층 | 기술 |
|------|------|
| 백엔드 | Python 3.12 · FastAPI · uvicorn |
| PTY (Windows) | pywinpty (`winpty.PtyProcess`) |
| PTY (Linux/Mac) | ptyprocess (`PtyProcessUnicode`) |
| 실시간 통신 | WebSocket (FastAPI 내장) |
| 프론트엔드 | Vanilla JS · Tailwind CSS CDN · xterm.js 4.19 + FitAddon |
| 폰트/아이콘 | Inter · JetBrains Mono · Material Symbols Outlined |
| 디자인 시스템 | `docs/ui/DESIGN.md` — "The Kinetic Monolith" (dark-only) |
| 런타임 의존 | `fastapi` `uvicorn[standard]` `pywinpty` `ptyprocess` |

---

## 3. 파일 구조

```
ai-company/
├── server.py              # FastAPI 백엔드 (전체 API + WebSocket)
├── static/
│   └── index.html         # 단일 파일 SPA (HTML + Tailwind + JS)
├── docs/
│   └── ui/
│       └── DESIGN.md      # Kinetic Monolith 디자인 시스템 명세
├── CLAUDE.md              # Claude Code 작업 규칙
├── TODO.md                # Playwright 점검 기반 잔여 작업 목록
├── requirements.txt
└── screenshots/           # git-ignored (Playwright 스크린샷)
```

---

## 4. 백엔드 (`server.py`)

### 4-1. 인메모리 상태

```python
sessions: dict   # session_id → PtyProcess  (수동 셸)
tasks: list      # 전체 태스크 목록 (dict)
cwd_locks: dict  # cwd → asyncio.Lock (같은 경로 태스크 직렬화)
```

서버 재시작 시 모든 상태 초기화 (영속성 없음 — T-4 기술 부채).

### 4-2. PTY 생성

```python
def spawn_pty(cmd, cwd=None, dimensions=(24, 80)):
    # Windows: winpty.PtyProcess.spawn()
    # Other:   ptyprocess.PtyProcessUnicode.spawn()
```

### 4-3. Git Auto-commit

```python
async def git_commit(cwd, message, output_buf=None):
```
- 태스크 실행 전/후에 `git add . && git commit` 자동 실행
- `output_buf`(태스크 출력 버퍼)를 전달하면 stderr를 `[git] ...` 형식으로 기록
- git이 없거나 변경사항 없어도 오류는 버퍼에 기록될 뿐 태스크 실행을 막지 않음

### 4-4. 태스크 실행 파이프라인 (`run_task`)

```
1. cwd별 Lock 획득 (동일 경로 태스크 직렬화)
2. status = "running", output = bytearray() 초기화
3. git_commit (Before)
4. spawn_pty: claude --dangerously-skip-permissions -p "..."
5. drain() 코루틴: PTY 출력 → output 버퍼 누적 + output_event 신호
6. proc.isalive() 폴링으로 종료 대기
7. status = "done" (외부 cancel이면 "cancelled" 유지)
8. git_commit (After)
```

### 4-5. REST API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/tasks` | 태스크 생성 및 비동기 실행 시작 |
| `GET` | `/api/tasks` | 전체 태스크 목록 반환 |
| `DELETE` | `/api/tasks/{id}` | 태스크 취소 (proc.close()) |
| `DELETE` | `/api/shells/{id}` | 수동 셸 PTY 종료 |
| `GET` | `/api/fs?path=` | 디렉터리 브라우저 (하위 디렉터리 목록) |

`/tasks` (prefix 없음)도 `/api/tasks`의 별칭으로 동시 노출.

### 4-6. WebSocket (`/ws/{session_id}`)

**태스크 세션** (`session_id.startswith("task-")`):
- 연결 즉시 `[잠시 대기 중...]` 메시지 전송
- `output_event` 초기화 대기 → 기존 누적 출력 재생 → 라이브 스트리밍
- 읽기 전용 (클라이언트 → 서버 방향 무시)

**셸 세션**:
- PTY가 없으면 PowerShell/bash 스폰
- 양방향: `pty_to_ws` + `ws_to_pty` 동시 실행
- WS 단절 시 PTY **유지** (`finally: pass`) — 재연결 시 동일 PTY 재사용
- JSON `{type: "resize", cols, rows}` → `proc.setwinsize()` 처리

---

## 5. 프론트엔드 (`static/index.html`)

단일 HTML 파일로 전체 UI 구현. 외부 번들러 없음 (Tailwind CDN).

### 5-1. 레이아웃

```
┌─────────────────────────────────────────────────┐
│  Sidebar (280px fixed)  │  Main (fluid)          │
│  ─ Logo + Add Task btn  │  ─ Header (64px)        │
│  ─ Shells section       │    · Session label      │
│    · Shell tabs (동적)  │    · Active Prompt      │
│  ─ Task Queue section   │    · Logs/Artifacts tabs│
│    · Task list (동적)   │    · Interrupt button   │
│  ─ Bottom nav links     │  ─ Terminal workspace   │
│                         │    (xterm.js)           │
└─────────────────────────────────────────────────┘
```

### 5-2. 상태 변수

```javascript
let ws = null;              // 현재 활성 WebSocket
let currentSession = null;  // 현재 선택된 session_id
let tasksList = [];         // 서버에서 폴링한 태스크 목록
let shellSessions = [];     // [{id, label}] 멀티 셸 배열
let shellCounter = 0;       // 셸 번호 카운터
const terminals = {};       // sessionId → {term, fitAddon, el}
```

### 5-3. 세션별 독립 xterm 인스턴스

```javascript
function getOrCreateTerminal(sessionId)
```
- 세션마다 `<div>` + `Terminal` + `FitAddon` 인스턴스를 생성
- 셸 전용 `onData`/`onResize`를 해당 인스턴스에 바인딩
- `connectTo()` 호출 시 모든 div `display:none` → 현재 세션 `display:block`
- **효과**: 탭 전환 후 돌아와도 이전 출력 그대로 유지
- `removeShell()`에서 `term.dispose() + el.remove()`로 정리

### 5-4. 핵심 함수

| 함수 | 역할 |
|------|------|
| `connectTo(sessionId)` | WS 연결, 터미널 전환, 헤더/버튼 상태 갱신 |
| `updateHeader(sessionId)` | 셸 → 셸 이름 표시 / 태스크 → cwd + prompt |
| `updateStopBtn(sessionId)` | 셸→회색 "Send Ctrl+C" / 실행중→빨간색 / 완료→비활성 |
| `updateActiveTab(sessionId)` | 사이드바 선택 상태 재렌더 |
| `addShell()` / `removeShell()` | 멀티 셸 탭 추가/삭제 |
| `renderShellTabs()` | 셸 탭 동적 렌더링 (CSS group-hover로 X 버튼 표시) |
| `renderTaskList()` | 태스크 사이드바 동적 렌더링 (상태별 스타일) |
| `stopCurrent()` | 셸 → Ctrl+C 전송 / 태스크 → DELETE API |
| `toggleModal(show)` | 태스크 생성 모달 열기/닫기 |
| `openBrowser()` / `goUp()` / `goHome()` / `selectDir()` | 디렉터리 브라우저 |
| `fetchDir(path)` | `GET /api/fs?path=` 호출 |

### 5-5. 태스크 상태별 UI

| 상태 | 사이드바 | Interrupt 버튼 |
|------|----------|----------------|
| `pending` | 파란 border-l | 빨간색 활성 |
| `running` | 초록 border-l + animate-pulse dot + running-glow | 빨간색 활성 |
| `done` | 파란 border-l (선택 시) | 비활성 (opacity-40) |
| `error` / `cancelled` | 주황 border-l (선택 시) | 비활성 (opacity-40) |

### 5-6. 기타 기능

- **Cmd/Ctrl+K**: 태스크 모달 열기
- **ESC**: 디렉터리 브라우저가 열려 있으면 브라우저만 닫기, 없으면 모달 닫기
- **localStorage**: `lastCwd` 저장 → 다음 방문 시 복원
- **2초 폴링**: `/api/tasks` 주기적 갱신 → 사이드바 + Interrupt 버튼 상태 동기화
- **페이지 새로고침 복원**: 초기화 시 `/api/tasks` 호출 → 서버 잔존 태스크 사이드바 복원
- **Favicon**: Data URI SVG ⚡ (404 없음)
- **Race condition 방지**: WS `onclose`/`onerror`에서 `ownSession` 클로저로 stale 이벤트 차단

---

## 6. 디자인 시스템 (Kinetic Monolith)

`docs/ui/DESIGN.md` 전체 명세. 핵심 원칙:

- **No-Line Rule**: 1px solid border 금지. 배경색 차이로만 경계 표현
- **Dark-only**: `surface` #131313 기준 5단계 표면 계층
- **Accent 절제**: `primary` #A4C9FF(파랑), `secondary` #45DFA4(초록) — 화면의 5% 이하
- **Typography**: Inter(UI) + JetBrains Mono(코드/ID/터미널)
- **Active Glow**: 실행 중 태스크에 `box-shadow: 0 0 15px rgba(96,165,250,0.2)`

---

## 7. 알려진 제한 및 잔여 부채

| ID | 구분 | 내용 |
|----|------|------|
| F-1 | 🔵 기능 | 태스크 완료/실패 사이드바 아이콘 (체크마크, 빨간 dot) |
| F-2 | 🔵 기능 | 헤더 ARTIFACTS / OUTPUT 탭 미연결 |
| F-3 | 🔵 기능 | 태스크 상세 메타 패널 (시작시간, 소요시간) |
| F-5 | 🔵 기능 | 셸 탭 이름 더블클릭 편집 |
| T-1 | ⚪ 부채 | Tailwind CDN → Vite + CLI 로컬 빌드 |
| T-3 | ⚪ 부채 | `GET /api/fs` 경로 제한 없음 (전체 파일시스템 접근 가능) |
| T-4 | ⚪ 부채 | 인메모리 상태 → SQLite 등 영속성 |
| T-5 | ⚪ 부채 | xterm.js Canvas2D readback 경고 (v5 업그레이드 검토) |

---

## 8. 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# 서버 실행
uvicorn server:app --reload --port 8000

# 브라우저
http://localhost:8000
```

`claude` CLI가 PATH에 있어야 태스크 실행 가능.
