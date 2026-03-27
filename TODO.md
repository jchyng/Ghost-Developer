# TODO

## 🟠 High

### 사이드바에서 실행 중인 채팅 구분 불가
여러 채팅 중 어떤 것이 현재 실행 중인지 알 수 없음.
- **해결 방향**: `setRunning(true)` 시 `activeChatId` 사이드바 아이템에 스피너/점멸 인디케이터 토글.

---

## 🟡 Medium

### 새 채팅 빈 화면에 아무 안내 없음
채팅 생성 직후 빈 입력창만 표시. 무엇을 해야 할지 모름.
- **해결 방향**: empty-state에 예시 프롬프트 3개 정도 칩(버튼) 형태로 표시.
  클릭하면 입력창에 자동 입력.

### 코드 블록 신택스 하이라이팅 없음
` ```lang ... ``` ` 멀티라인 코드블록이 그냥 텍스트로 렌더링.
- **해결 방향**: `renderMarkdownLite`에 멀티라인 코드블록 파싱 추가.
  highlight.js CDN으로 신택스 컬러링 적용.

### lastCwd 재오픈 시 폴더 목록 지연
`closeNewChatModal`에서 `browseState` 리셋 후 재오픈하면 드롭다운이 잠깐 빈 상태로 나타남.
- **해결 방향**: `openNewChatModal`에서 input value 세팅 직후 `fetchDir` 선제 호출
  (setTimeout 안에서 처리해 input.focus 이전에 데이터 준비).

---

## 🔵 Low

### 메시지 타임스탬프 없음
언제 대화가 오갔는지 알 수 없음.
- **해결 방향**: 날짜가 바뀌는 지점에 날짜 구분선. 또는 메시지 hover 시 시간 툴팁.

### 이름 변경 후 헤더 cwd 미갱신
채팅 title을 바꿔도 헤더의 cwd 서브텍스트가 남아 있는 경우가 있을 수 있음.
- **해결 방향**: `startInlineRename` commit 시 `selectChat`과 동일한 헤더 업데이트 로직 호출.
