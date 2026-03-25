# AI Company

1인 개발자를 위한 자율형 코딩 에이전트 시스템.
웹 브라우저에서 작업을 지시하면, 서버의 터미널에서 **Claude Code**가 자율적으로 코딩을 수행한다.

## 핵심 철학

- **Thin Wrapper**: 서버는 지능이 없는 순수 파이프. 지능은 전적으로 Claude Code에 위임.
- **Zero-Overhead**: 빌드 툴 없음. 순수 HTML/JS + FastAPI.
- **YAGNI**: 지금 당장 필요한 것만 구현.

자세한 원칙은 [`docs/core/`](docs/core/) 참고.

---

## 프로젝트 구조

```
ai-company/
│
├── server.py            # 백엔드: FastAPI + PTY 브리지 + 태스크 큐
├── static/
│   └── index.html       # 프론트엔드: xterm.js 터미널 UI (빌드 툴 없음)
│
├── requirements.txt     # Python 의존성 (fastapi, uvicorn, pywinpty)
├── CLAUDE.md            # 이 레포에서 Claude Code가 따르는 규칙
├── .gitignore
│
└── docs/
    ├── core/            # 기획 문서 (모든 의사결정의 기준)
    │   ├── 0_대화_및_개발_규칙.md
    │   ├── 1_핵심_원칙.md
    │   ├── 2_MVP_기획서.md
    │   └── 3_향후_확장계획.md
    └── ui/              # UI 레퍼런스
        ├── 새_UI_프로토타입.html
        └── screenshots/ # 프로토타입 스크린샷
```

---

## 아키텍처

```
브라우저 (xterm.js)
    │  WebSocket (binary stream)
    ▼
server.py (FastAPI)          ← 멍청한 파이프. 지능 없음.
    │  PTY (stdin/stdout)
    ▼
claude --dangerously-skip-permissions -p "..."
    │  파일시스템 조작
    ▼
프로젝트 디렉토리 (cwd)
```

**태스크 흐름:**
1. 브라우저에서 `cwd` + 프롬프트 제출
2. 서버가 해당 `cwd`에서 Claude Code를 PTY로 실행
3. Claude의 터미널 출력이 WebSocket으로 브라우저에 실시간 스트리밍
4. 작업 전/후 자동 `git commit` (롤백 보험)

---

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# 서버 실행
uvicorn server:app --reload

# 브라우저에서 접속
# → http://localhost:8000
```

---

## API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/tasks` | 작업 추가 `{ cwd, prompt }` |
| `GET`  | `/tasks` | 전체 작업 목록 조회 |
| `DELETE` | `/tasks/{id}` | 실행 중인 작업 강제 종료 |
| `WS`   | `/ws/{session_id}` | PTY ↔ 브라우저 바이너리 중계 |
