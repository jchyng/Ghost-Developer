# Ghost Developer - 기획서

---

## v1.0 — 구현 완료

> **목표:** 웹 브라우저에서 작업 지시를 내리면, 서버의 PTY에서 Claude Code가 `--dangerously-skip-permissions`로 코딩을 수행하는 단일 핵심 루프 완성.

**구현 완료 항목:**
- 태스크 큐 (In-Memory) + cwd별 asyncio.Lock 직렬화
- Claude Code PTY 실행 + 실시간 WebSocket 스트리밍
- git auto-commit Before/After
- 수동 멀티 Shell 탭 (PowerShell/bash), PTY는 WS 단절 후 유지
- 세션별 독립 xterm 인스턴스 (탭 전환 후 복귀 시 이전 출력 유지)
- 서버 사이드 디렉터리 브라우저 (`GET /api/fs`)
- 태스크 강제 종료 (`DELETE /api/tasks/{id}`)
- 페이지 새로고침 후 태스크 사이드바 자동 복원

**v1의 한계:**
- `claude -p` 단발 실행 → 중간 개입 불가, 대화 불가
- 작업 완료 후 이어서 지시하거나 방향 수정 불가
- 서버 재시작 시 모든 기록 소실

---

## v2.0 — 개발 예정

> **목표:** 채팅으로 지시하면 Orchestrator LLM이 Claude Code를 자율적으로 조작해 작업을 완료한다. 사용자는 언제든 개입할 수 있고, 세션은 영구 보존된다.

### 핵심 아키텍처

```
사용자 (채팅)
    ↕
Orchestrator LLM (gpt-5.4-mini)
    ↕  claude -p "..." --session-id X --output-format json
Claude Code (headless)
    ↕  tool executions (파일 수정, bash 실행 등)
파일시스템 / 터미널들
```

**Orchestrator**는 Claude Code를 headless 모드로 호출하고 JSON 응답을 받아 다음 행동을 결정한다. 사용자는 채팅으로 Orchestrator에게 지시하거나, 터미널에 직접 타이핑해 Claude Code session에 개입할 수 있다.

---

### A. Chat-Primary UI

- **사이드바**: 채팅 세션 목록 (Recent Chats) + New Chat 버튼
- **메인 영역**: 채팅창 (primary) + 터미널 패널 (토글, secondary)
- Add Task 모달 폐기 → 채팅 입력창으로 대체
- 채팅 히스토리 SQLite 영구 저장

### B. Orchestrator 루프

1. 사용자 메시지 수신
2. `claude -p "지시" --resume X --dangerously-skip-permissions --output-format stream-json --verbose` 호출
3. JSON 응답 파싱 (결과, tool use 목록, token usage)
4. 결과를 채팅창에 표시
5. **작업 완료 판단** → 완료 아니면 2로 반복

**작업 완료 판단 기준:**
- JSON 응답에 더 이상 수행할 tool_use가 없고, 응답 텍스트가 작업 완료를 나타낼 때
- Orchestrator(gpt-5.4-mini)가 Claude Code의 마지막 응답을 읽고 "완료됐는지"를 판단
- 판단 불가 시 사용자에게 채팅으로 확인 요청

**session_id 관리:**
- 첫 호출 시 `--output-format json`으로 session_id 추출, DB 저장
- 이후 모든 호출에 `--session-id X` 사용 → 대화 연속성 유지
- 서버 재시작, 브라우저 새로고침 후에도 동일 session 재개 가능

**Chat 세션 생성:**
- "New Chat" 클릭 → cwd(작업 디렉토리) 입력 → 채팅 시작
- cwd는 이후 모든 claude -p 호출의 working directory로 사용

### C. 컨텍스트 관리

- 매 응답의 `usage.input_tokens` 추적
- 임계치(예: 80%) 도달 시 Orchestrator가 직접 compact 수행
  - `claude -p "/compact" --session-id X` 호출
- auto-compact에 의한 컨텍스트 손실 방지

### D. Rate Limit 자동 스케줄링

- Claude Code 응답이 rate limit 에러일 경우 `retry_after` 타임스탬프 추출
- 해당 시간 이후 자동 재개 스케줄링 (SQLite에 상태 저장)
- 서버 재시작 후에도 스케줄 복원

### E. 멀티 터미널 (Chat 세션 소속)

**터미널 패널 구성:**
- `[Claude Code]` 탭: Orchestrator가 보낸 `claude -p` 명령과 응답을 실시간 로그로 표시 (읽기 전용 뷰 + 직접 입력 가능)
  - 사용자가 직접 타이핑 → `claude -p "입력 내용" --session-id X`로 즉시 전송 (Orchestrator 우회)
  - 단, 직접 입력한 내용과 그 응답은 Orchestrator의 다음 컨텍스트에 자동 포함
- `[+]` 탭: Orchestrator 또는 사용자가 추가하는 보조 PTY 터미널 (dev server, test runner 등)
  - 일반 PTY 세션으로, 양방향 입출력 가능 (기존 Shell 탭과 동일한 방식)

**리소스 관리:**
- 미사용 PTY 터미널 자동 종료 (tmux 방식: 프로세스 종료, scrollback은 DB 보존)
- Chat 세션 삭제 시 소속 터미널 전체 삭제

**Orchestrator 도구:**
- `open_terminal(name, cwd)` → 새 PTY 탭 생성
- `write_to_terminal(id, text)` → PTY stdin 입력
- `read_terminal(id)` → 최근 출력 읽기

### F. git Auto-Commit (v1에서 유지)

- **트리거**: 사용자가 채팅에 메시지를 보낼 때마다 (= Orchestrator가 Claude Code를 처음 호출하기 직전)
- Before: `Auto-commit: Before [사용자 메시지 앞 50자]`
- After: `Auto-commit: After [사용자 메시지 앞 50자]` (Orchestrator가 작업 완료 판단 후)
- cwd는 Chat 세션에 설정된 경로 사용

### G. 동시 실행

- 여러 Chat 세션이 각자의 Orchestrator를 가지고 병렬 실행 가능
- cwd별 Lock은 유지 (동일 디렉토리 동시 작업 방지)

---

### 제외 항목 (v2 대상 아님)

- 사용자 인증 / 멀티 유저
- Telegram 알림
- Circuit Breaker (자동 루프 감지)
