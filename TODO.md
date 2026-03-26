# TODO — Kinetic AI MVP

> Playwright 점검 결과 기반 (2026-03-26)
> 우선순위: 🔴 버그 / 🟡 UX 개선 / 🔵 기능 / ⚪ 기술 부채

---

## 🔴 버그 (즉시 수정 필요)

### B-1. `[disconnected]` race condition
- **현상**: 셸 탭 전환 시 이전 WS의 `onclose`가 `term.clear()` 이후 비동기로 실행되어 새 세션 터미널에 `[disconnected]` 텍스트가 남음
- **재현**: Shell 1 활성 상태에서 Shell 2로 전환 → 터미널에 `[disconnected]` 출력
- **원인**: `connectTo()`에서 `ws.close()` 직후 `term.clear()` 호출하지만, `onclose` 콜백은 이벤트 루프 다음 tick에 실행됨
- **Fix**: `onclose` 내부에서 세션 ID 캡처 후 `sessionId === currentSession`일 때만 메시지 출력

### B-2. 디렉터리 브라우저 — ESC가 모달 전체를 닫음
- **현상**: 브라우저 팝오버가 열린 상태에서 ESC → 모달 전체가 닫힘 (팝오버만 닫혀야 함)
- **Fix**: ESC 키 핸들러에서 팝오버가 열려 있으면 팝오버만 닫고 이벤트 전파 중단

---

## 🟡 UX 개선

### U-1. 셸 탭 X 버튼 가시성
- **현상**: 활성 탭에만 X가 보임 (비활성 탭은 hover 시에만 표시 의도였지만 Playwright 환경 및 실사용에서 발견하기 어려움)
- **개선안**: 모든 탭에 X를 항상 표시하거나, hover CSS(`:hover`)로 처리 — 현재 inline `onmouseenter/onmouseleave` 방식은 fragile

### U-2. Task Queue 빈 상태 (empty state)
- **현상**: 태스크가 없을 때 "Task Queue" 아래 아무것도 없음 — 사용자가 어떻게 써야 할지 모름
- **개선안**: `+ Add Task 버튼을 눌러 첫 태스크를 추가하세요` 가이드 텍스트 추가

### U-3. 터미널 세션 전환 시 히스토리 유지 없음
- **현상**: Shell 1 → Shell 2 전환 후 Shell 1로 되돌아오면 터미널이 비어 있음 (단순 clear)
- **원인**: xterm 인스턴스가 하나뿐 — 세션별 스크롤백 버퍼 미존재
- **개선안 A (단기)**: 세션별 출력 버퍼를 JS 메모리에 저장 후 전환 시 재생
- **개선안 B (장기)**: xterm 인스턴스를 세션마다 생성, CSS `display: none`으로 전환

### U-4. Current Context가 실제 PTY 경로를 반영하지 않음
- **현상**: 헤더의 `Current Context`는 항상 `~` — shell에서 `cd /dev` 해도 변하지 않음
- **개선안**: shell 세션에서 실제 CWD 추적은 복잡하므로, 현재 연결된 세션 이름(Shell 1 등)만 표시하거나 라벨을 "Session"으로 변경

### U-5. Interrupt 버튼 상태 구분 없음
- **현상**: `INTERRUPT (CTRL+C)` 버튼이 항상 빨간색으로 활성화 표시 — 실행 중인 태스크가 없어도 동일
- **개선안**: 셸 탭 선택 시 → "Send Ctrl+C" 레이블 + 보조 스타일, 실행 중 태스크 선택 시 → 현재 스타일 유지

### U-6. 디렉터리 브라우저 — 홈 바로가기 없음
- **현상**: 루트까지 올라간 후 홈으로 되돌아올 방법이 없음
- **개선안**: 팝오버 상단에 🏠 홈 버튼 추가

---

## 🔵 미구현 기능

### F-1. 태스크 실행 후 실시간 사이드바 상태 반영
- 현재 폴링(2초)은 있지만 태스크가 `running → done`으로 바뀔 때 사이드바 탭 시각적 피드백이 약함
- 완료 시 체크마크, 에러 시 빨간 dot 등 상태 아이콘 추가

### F-2. Header LOGS / ARTIFACTS / OUTPUT 탭
- 현재 클릭 가능하지만 아무 동작 없음
- 최소한 `LOGS` → 현재 터미널, `OUTPUT` → 태스크 최종 결과 출력 정도로 연결

### F-3. 태스크 상세 보기
- 태스크 탭 클릭 시 터미널만 보여줌 — cwd, 전체 prompt, 시작 시간 등 메타 정보 패널 없음

### F-4. 페이지 새로고침 후 태스크 복원
- 서버 재시작 전까지는 `/api/tasks`로 태스크 목록이 살아있지만, 프론트엔드가 새로고침되면 사이드바 탭이 초기화됨
- 초기 로딩 시 `GET /api/tasks` 결과로 사이드바 복원 필요 (서버 코드는 이미 지원)

### F-5. 셸 탭 이름 변경 (더블클릭 편집)
- 현재 Shell 1, Shell 2 고정 — 더블클릭으로 커스텀 이름 지정

---

## ⚪ 기술 부채

### T-1. Tailwind CDN → 로컬 빌드
- 현재 `cdn.tailwindcss.com` 사용 → 프로덕션 배포 불가, 브라우저 콘솔 경고 발생
- Vite + Tailwind CLI로 빌드 파이프라인 구성 필요

### T-2. favicon.ico 없음
- 매 요청마다 404 로그 발생
- 간단한 favicon 추가 또는 `<link rel="icon" href="data:,">` 처리

### T-3. `GET /api/fs` 보안 범위 제한 없음
- 현재 서버의 전체 파일시스템 탐색 가능
- 프로덕션 고려 시: 허용 경로 whitelist 또는 홈 디렉터리 이하로 제한

### T-4. 인메모리 태스크/세션 관리
- 서버 재시작 시 모든 태스크, 세션 소실
- SQLite 등 경량 영속성 레이어 추가 검토

### T-5. xterm Canvas2D readback 경고
- `willReadFrequently` 속성 미설정으로 성능 경고 발생
- xterm.js 5.x 업그레이드 또는 초기화 옵션 설정으로 해결

---

## 작동 확인된 항목 ✅

| 항목 | 결과 |
|------|------|
| 디렉터리 브라우저 폴더 네비게이션 | ✅ 정상 |
| "Select This Directory" → cwd 입력 채우기 | ✅ 정상 |
| 루트(`C:\`)에서 상위 이동 시 제자리 유지 | ✅ 정상 |
| cwd localStorage 자동 복원 | ✅ 정상 |
| ESC로 모달 닫기 | ✅ 정상 |
| 멀티 셸 탭 생성(`+` 버튼) | ✅ 정상 |
| X 버튼 → 셸 종료 및 다른 탭으로 자동 전환 | ✅ 정상 |
| 마지막 셸 닫을 때 새 탭 자동 생성 | ✅ 정상 |
| 모달 배경 클릭으로 닫기 | ✅ 정상 |
| Task Queue / Shell Sessions 섹션 구분 | ✅ 정상 |
| Kinetic Monolith 디자인 시스템 적용 | ✅ 정상 |
