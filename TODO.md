# TODO — Kinetic AI MVP

> Playwright 점검 결과 기반 (2026-03-26)
> 우선순위: 🔴 버그 / 🟡 UX 개선 / 🔵 기능 / ⚪ 기술 부채

---

## ✅ 처리 완료

| 항목 | 내용 |
|------|------|
| B-1 | 셸 전환 시 `[disconnected]` race condition 수정 |
| B-2 | ESC 키가 디렉터리 브라우저 열린 상태에서 모달 전체 닫는 버그 수정 |
| F-4 | 페이지 새로고침 후 태스크 사이드바 복원 |
| U-2 | Task Queue 빈 상태 안내 텍스트 추가 |
| T-2 | favicon 404 오류 제거 |

---

## 🔴 버그

_(완료된 항목 없음 — B-1, B-2 처리됨)_

---

## 🟡 UX 개선

### U-1. 셸 탭 X 버튼 가시성
- **현상**: 비활성 탭의 X 버튼이 평소에 보이지 않음 (inline onmouseenter/onmouseleave 방식)
- **개선안**: CSS `:hover` 로 처리하거나 항상 노출

### U-3. 터미널 세션 전환 시 히스토리 유지 없음
- **현상**: Shell 1 → Shell 2 전환 후 돌아오면 터미널 비어 있음
- **개선안 A (단기)**: 세션별 출력 버퍼를 JS 메모리에 저장 후 전환 시 재생
- **개선안 B (장기)**: xterm 인스턴스를 세션마다 생성, CSS `display:none`으로 전환

### U-4. Current Context 헤더 의미 불명확
- **현상**: 셸 탭 선택 시 항상 `~` 표시 — 실제 PTY 경로 미반영
- **개선안**: 라벨을 "Session"으로 변경하거나 셸 이름(Shell 1 등)으로 표시

### U-5. Interrupt 버튼 상태 구분 없음
- **현상**: 셸/태스크 무관하게 항상 동일한 빨간색 표시
- **개선안**: 셸 → "Send Ctrl+C" 보조 스타일, 실행 중 태스크 → 현재 스타일

### U-6. 디렉터리 브라우저 — 홈 바로가기 없음
- **개선안**: 팝오버 상단에 🏠 홈 버튼 추가

---

## 🔵 미구현 기능

### F-1. 태스크 완료/실패 시 사이드바 상태 아이콘
- 완료 → 체크마크, 에러 → 빨간 dot 등 추가

### F-2. Header LOGS / ARTIFACTS / OUTPUT 탭 연결
- 현재 클릭 불가 — 최소한 LOGS → 현재 터미널 연결

### F-3. 태스크 상세 메타 패널
- cwd, 전체 prompt, 시작시간, 소요시간 표시

### F-5. 셸 탭 이름 변경 (더블클릭 편집)

---

## ⚪ 기술 부채

### T-1. Tailwind CDN → 로컬 빌드
- Vite + Tailwind CLI 파이프라인 구성

### T-3. `GET /api/fs` 보안 범위 제한
- 전체 파일시스템 탐색 가능 — 허용 경로 제한 검토

### T-4. 인메모리 태스크/세션 관리 → 영속성
- 서버 재시작 시 모든 상태 소실 — SQLite 등 검토

### T-5. xterm Canvas2D readback 경고
- xterm.js 5.x 업그레이드 검토

---

## 🔬 End-to-End 분석 결과 (2026-03-26)

Playwright + API 직접 호출로 태스크 실행 파이프라인 전체를 검증함.

### 확인된 정상 동작 ✅
| 항목 | 결과 |
|------|------|
| `POST /api/tasks` 응답 | 즉시 task 객체 반환 (`pending`) |
| PTY 스폰 (`winpty`) | 정상 (`PtyProcess.isalive() = True`) |
| 터미널 → WebSocket → xterm 렌더링 | `PS C:\dev\ai-company>` 출력 확인 |
| 태스크 클릭 시 헤더 업데이트 | cwd, prompt 정상 반영 |
| 사이드바 RUNNING 상태 표시 | 초록 dot + 애니메이션 확인 |
| `DELETE /api/tasks/{id}` 취소 | 즉시 `cancelled` 상태 반환 |
| 페이지 새로고침 후 태스크 복원 | 사이드바에 RUNNING 탭 자동 복원 |
| favicon 404 오류 | 해결됨 (콘솔 에러 0개) |

### 발견된 이슈 🟡
| 항목 | 내용 |
|------|------|
| 태스크 시작 시 로딩 표시 없음 | `pending` 상태에서 터미널이 비어있어 사용자가 진행 여부 알 수 없음 |
| claude CLI 시작 지연 | API 초기화 + 네트워크 호출로 수십 초 소요 — 진행 스피너 필요 |
| git auto-commit 소요 시간 | 태스크 실행 전 `git add . && git commit` 이 blocking으로 실행됨 |

### 추가 권고 사항
- **`pending` 상태 터미널**: `"Waiting for agent to start..."` 메시지를 WS 연결 전 xterm에 출력
- **git auto-commit 비동기화**: 현재 commit이 완료된 후에야 claude가 실행됨. 실패 시 silent하므로 로그 필요
- **태스크 타임아웃**: 무한 실행 방지를 위한 최대 실행 시간 설정 검토
