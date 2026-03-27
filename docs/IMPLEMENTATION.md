# Ghost Developer — 구현 현황 문서

> 최종 업데이트: 2026-03-27

---

## 1. 프로젝트 개요

나를 대신해서 개발하는 AI — 1인 개발자를 위한 자율형 코딩 에이전트 시스템.
브라우저에서 채팅으로 지시하면 Orchestrator LLM이 Claude Code를 자율적으로 실행하고, 결과를 실시간으로 스트리밍한다.

---

## 2. 기술 스택

| 계층 | 기술 |
|------|------|
| 백엔드 | Python 3.12 · FastAPI · uvicorn |
| Orchestrator | OpenAI (`gpt-4o-mini`) — Claude Code 호출 전략 결정 |
| PTY (Windows) | pywinpty (`winpty.PtyProcess`) |
| PTY (Linux/Mac) | ptyprocess (`PtyProcessUnicode`) |
| 실시간 통신 | WebSocket (FastAPI 내장) |
| DB | SQLite3 (WAL mode) — 채팅·메시지·스케줄 영속 저장 |
| 프론트엔드 | Vanilla JS · Tailwind CSS CDN · xterm.js 4.19 · highlight.js 11.9 |
| 폰트/아이콘 | Inter · JetBrains Mono · Lucide Icons |
| 디자인 시스템 | `docs/ui/DESIGN.md` — "The Kinetic Monolith" (dark-only) |

---

## 3. 파일 구조

```
ghost-developer/
├── server.py              # FastAPI 백엔드 — API, WebSocket, Orchestrator 연동
├── orchestrator.py        # Orchestrator 루프 — Claude Code 반복 호출 판단
├── claude_caller.py       # Claude Code CLI 호출 래퍼 (PTY)
├── db.py                  # SQLite CRUD (chats, messages, schedules, terminals)
├── static/
│   ├── index.html         # 단일 파일 SPA
│   └── logo.png           # Ghost Developer 로고
├── docs/
│   ├── core/              # 기획 문서
│   ├── ui/
│   │   ├── DESIGN.md      # Kinetic Monolith 디자인 시스템
│   │   └── 컨셉이미지.png
│   └── IMPLEMENTATION.md  # 이 문서
├── CLAUDE.md              # Claude Code 작업 규칙
├── requirements.txt
└── .env                   # OPENAI_API_KEY 등 (git-ignored)
```

---

## 4. 아키텍처

```
브라우저 (채팅 UI + xterm.js 터미널)
    │  WebSocket (/ws/chat/{chat_id})
    ▼
server.py (FastAPI)
    │  asyncio.create_task
    ▼
orchestrator.py (Orchestrator 루프)
    │  claude -p "..." --session-id X --output-format stream-json
    ▼
Claude Code (headless, PTY)
    │  파일시스템 조작, bash 실행
    ▼
프로젝트 디렉토리 (cwd)
```

---

## 5. 백엔드 (`server.py`)

### API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/chats` | 채팅 목록 |
| `POST` | `/api/chats` | 새 채팅 생성 `{cwd, title}` |
| `DELETE` | `/api/chats/{id}` | 채팅 삭제 (CASCADE) |
| `PATCH` | `/api/chats/{id}` | 채팅 제목 변경 `{title}` |
| `GET` | `/api/chats/{id}/messages` | 메시지 히스토리 |
| `POST` | `/api/chats/{id}/send` | 메시지 전송 → Orchestrator 실행 |
| `DELETE` | `/api/chats/{id}/cancel` | 실행 중 Orchestrator 강제 종료 |
| `GET` | `/api/fs?path=` | 디렉터리 브라우저 |

### WebSocket

- `/ws/chat/{chat_id}` — 채팅 이벤트 스트리밍 (JSON)
- `/ws/terminal/{terminal_id}` — PTY 양방향 바이너리 스트리밍

### 이벤트 타입 (WebSocket → 클라이언트)

| type | 설명 |
|------|------|
| `history` | 연결 시 기존 메시지 전체 전송 |
| `message` | AI 응답 메시지 |
| `result` | Orchestrator 최종 결과 |
| `status` | 상태 메시지 (info/warn/error) |
| `title_update` | 자동 생성된 채팅 제목 |
| `done` | Orchestrator 실행 완료 |

### 자동 제목 생성

첫 메시지 전송 시 `_generate_title()` 비동기 실행 → `gpt-4o-mini`로 4-6단어 제목 생성 → `title_update` 이벤트로 실시간 반영.

---

## 6. DB 스키마 (`db.py`)

```sql
chats      (id, cwd, title, session_id, created_at)
messages   (id, chat_id, role, content, created_at)
terminals  (id, chat_id, name, cwd, created_at)
schedules  (id, chat_id, resume_at, created_at)  -- rate limit 재개 예약
```

---

## 7. 프론트엔드 (`static/index.html`)

단일 HTML 파일. 빌드 툴 없음.

### 주요 전역 상태

```javascript
let activeChatId = null;      // 현재 선택된 채팅 ID
let activeChatData = null;    // 현재 채팅 메타데이터 (cwd, title 등)
let chatWs = null;            // 채팅 WebSocket
let isRunning = false;        // Orchestrator 실행 중 여부
```

### 핵심 함수

| 함수 | 역할 |
|------|------|
| `loadChats()` | 채팅 목록 로드, 없으면 웰컴 화면 |
| `selectChat(id, chat)` | 채팅 선택, WS 연결, 메시지 로드 |
| `sendMessage()` | 메시지 전송, Orchestrator 실행 트리거 |
| `setRunning(val)` | 실행 상태 토글 (타이핑 인디케이터, 사이드바 dot) |
| `renderMarkdownLite(text)` | 마크다운 렌더링 + highlight.js 코드 하이라이팅 |
| `updateHeader(chat)` | 헤더 제목/cwd 업데이트 |
| `openNewChatModal()` | 새 채팅 모달 (디렉터리 브라우저 포함) |
| `confirmDeleteChat(id)` | 인라인 2단계 삭제 확인 |
| `startInlineRename(id)` | 인라인 제목 편집 |

### 단축키

| 키 | 동작 |
|----|------|
| `Ctrl+K` | 새 채팅 모달 열기 |
| `Ctrl+Enter` | 메시지 전송 |
| `Enter` | 줄바꿈 |
| `Tab` | 디렉터리 자동완성 |
| `Esc` | 디렉터리 브라우저 닫기 |

---

## 8. 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# .env 설정
echo "OPENAI_API_KEY=sk-..." > .env

# 서버 실행
uvicorn server:app --reload --port 8000

# 브라우저
http://localhost:8000
```

`claude` CLI가 PATH에 있어야 Orchestrator 실행 가능.
