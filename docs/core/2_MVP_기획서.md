# AI Company - MVP 기획서 (v1.0) — 구현 완료

> **목표:** 내가 웹 브라우저에서 작업 지시를 내리면, 서버의 터미널(PTY)에서 Claude Code가 `dangerously-skip-permissions` 상태로 완벽하게 코딩을 수행하는 **'단일 핵심 루프'**를 완성한다.

## 1. 핵심 아키텍처 (Thin Wrapper)
*   **프론트엔드 (Web UI):** 
    *   단일 페이지 애플리케이션 (HTML/JS/CSS).
    *   `xterm.js`를 사용한 브라우저 터미널 에뮬레이터.
    *   여러 프로젝트(작업 디렉토리)를 탭 형태로 관리.
*   **백엔드 (Python / FastAPI):**
    *   Web UI와 로컬 터미널 간의 브리지 역할만 수행.
    *   Python `pty` 모듈을 사용하여 자식 프로세스 생성 및 관리.
    *   WebSocket을 통해 `xterm.js`와 PTY(stdin/stdout) 간 양방향 바이트 스트림 중계.
*   **AI 엔진 (Claude Code):**
    *   모든 지능, 권한, 에이전트 관리는 전적으로 Claude Code에 위임.
    *   실행 옵션: `claude --dangerously-skip-permissions` (모든 권한 허용, 무한 직진).

## 2. MVP 핵심 기능

### A. 작업 큐 및 프로세스 관리 (In-Memory) ✅
*   복잡한 DB(SQLite) 없이 파이썬 딕셔너리/리스트를 활용해 In-Memory로 상태 관리.
*   **작업(Task) 구조:** `{ id, cwd, prompt, status: 'pending'|'running'|'done'|'error'|'cancelled' }`
*   사용자가 프롬프트와 프로젝트 경로(`cwd`)를 입력하면 큐에 추가.
*   **Mutex (동시성 제어):** 동일한 `cwd`를 가진 작업은 동시에 실행되지 않고 순차적으로 대기.
*   태스크 강제 종료 (`DELETE /api/tasks/{id}`) 추가 구현.
*   페이지 새로고침 후 서버 잔존 태스크를 사이드바에 자동 복원.

### B. 터미널 멀티플렉싱 ✅
*   세션마다 독립적인 xterm.js 인스턴스 유지 → 탭 전환 후 복귀 시 이전 출력 그대로 보존.
*   Claude Code의 기본 터미널 출력(ANSI 색상, 스피너 등)이 깨지지 않고 웹에 렌더링.
*   수동 인터랙티브 셸(PowerShell/bash) 멀티 탭 추가 구현 — WS 단절 시 PTY 유지, 재연결 시 재사용.

### C. 최소한의 안전장치: Auto-Commit ✅
*   AI가 코드를 망치는 것을 대비한 유일하고 가장 확실한 보험.
*   백엔드 로직:
    1. 작업 시작 직전: 해당 `cwd`에서 `git add . && git commit -m "Auto-commit: Before [Task Name]"` 자동 실행.
    2. 작업 완료 직후: `git add . && git commit -m "Auto-commit: After [Task Name]"` 자동 실행.
*   git stderr를 태스크 터미널 출력에 `[git] ...` 형식으로 기록 (오류 무음 처리 해소).

### D. 프로젝트별 규칙 제어 (CLAUDE.md) ✅
*   서버 단에서 역할을 지정하지 않는다.
*   각 프로젝트의 루트 경로에 있는 `CLAUDE.md` 파일에 해당 프로젝트의 코딩 컨벤션, 사용할 서브에이전트, 프롬프트 엔지니어링을 기록한다. Claude Code가 이를 알아서 읽고 따른다.

### E. 기획 외 추가 구현 (실사용 필요 확인 후 반영)
*   **서버 사이드 디렉터리 브라우저** (`GET /api/fs`): 브라우저 보안 제약으로 `showDirectoryPicker()`가 전체 경로를 반환하지 않아 서버 측 탐색으로 대체.
*   **cwd localStorage 복원**: 마지막으로 사용한 경로를 기억해 모달 재진입 시 자동 입력.
*   **Pending 터미널 메시지**: 태스크 WS 연결 즉시 "[잠시 대기 중...]" 출력으로 빈 터미널 체감 해소.

## 3. 제외된 기능 (MVP 대상 아님)
*   사용자 로그인 / 인증 시스템 (로컬 혼자 사용)
*   Telegram 봇 연동 및 원격 알림
*   GPT 기반의 프록시 및 출력 모니터링 (자동 판단/질문 로직)
*   SQLite를 이용한 작업 이력 영구 저장
*   복잡한 Circuit Breaker (무한 루프 감지기)
