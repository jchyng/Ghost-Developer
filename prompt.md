내가 Stitch AI를 통해 MVP에 적용할 새로운 프론트엔드 UI 코드(index.html)를 뽑아왔어. (아래 코드 블록 참고)

이 새로운 UI 디자인을 결합해서 완벽한 MVP를 완성해줘.

[작업 목표]

1. 프론트엔드 (`static/index.html`) 업데이트:
   - 내가 제공하는 UI 코드의 `<div id="terminal">` 안에 기존의 가짜 텍스트들을 전부 지우고, 아까 우리가 작성했던 `xterm.js` 연동 스크립트와 스타일을 적용해줘.
   - 좌측 사이드바의 "+ Add Task" 모달에서 폼을 제출(Submit)하면, 입력된 `cwd`와 `prompt` 값을 추출해서 백엔드의 `POST /api/tasks` 로 전송(fetch)하게 만들어줘.
2. 백엔드 (`server.py`) API 추가 및 수정:
   - `POST /api/tasks` 엔드포인트를 추가해. 이 API는 프롬프트와 경로를 받아서 In-Memory 작업 큐(Queue)에 넣는 역할을 해.
   - 작업을 큐에 넣은 후, 서버 측에서 `pty`를 띄울 때 단순 셸(powershell/bash)이 아니라 `claude --dangerously-skip-permissions "{prompt}"` 명령어를 해당 `cwd` 경로에서 실행하도록 연결해줘. (OS 분기 로직은 유지)

아래가 내가 준비한 새로운 UI 코드야:

```html
<!DOCTYPE html>

<html class="dark" lang="en">
  <head>
    <meta charset="utf-8" />
    <meta content="width=device-width, initial-scale=1.0" name="viewport" />
    <title>Kinetic AI | Task Runner</title>
    <script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
    <link
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&amp;family=JetBrains+Mono:wght@400;500&amp;display=swap"
      rel="stylesheet"
    />
    <link
      href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap"
      rel="stylesheet"
    />
    <link
      href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap"
      rel="stylesheet"
    />
    <script id="tailwind-config">
      tailwind.config = {
        darkMode: "class",
        theme: {
          extend: {
            colors: {
              "on-error-container": "#ffdad6",
              "outline-variant": "#414751",
              "inverse-surface": "#e5e2e1",
              "surface-container-lowest": "#0e0e0e",
              "surface-bright": "#3a3939",
              "on-secondary-fixed-variant": "#005137",
              "on-primary-fixed": "#001c39",
              "secondary-container": "#00bd85",
              "primary-container": "#60a5fa",
              "on-secondary": "#003825",
              "on-background": "#e5e2e1",
              tertiary: "#fabd34",
              "on-primary": "#00315d",
              "tertiary-container": "#d19900",
              "on-primary-fixed-variant": "#004883",
              "on-error": "#690005",
              "surface-variant": "#353534",
              "secondary-fixed-dim": "#45dfa4",
              surface: "#131313",
              "inverse-primary": "#0060ac",
              "on-secondary-container": "#00452e",
              "on-surface-variant": "#c1c7d3",
              "on-surface": "#e5e2e1",
              "surface-container-highest": "#353534",
              "on-tertiary-fixed": "#261900",
              background: "#131313",
              "inverse-on-surface": "#313030",
              "on-tertiary-container": "#4b3500",
              "tertiary-fixed-dim": "#fabd34",
              "primary-fixed": "#d4e3ff",
              "tertiary-fixed": "#ffdea4",
              "on-tertiary-fixed-variant": "#5d4200",
              secondary: "#45dfa4",
              "surface-container-high": "#2a2a2a",
              "surface-container": "#201f1f",
              "surface-container-low": "#1c1b1b",
              "surface-dim": "#131313",
              "secondary-fixed": "#68fcbf",
              outline: "#8b919d",
              "on-tertiary": "#412d00",
              "error-container": "#93000a",
              "primary-fixed-dim": "#a4c9ff",
              error: "#ffb4ab",
              primary: "#a4c9ff",
              "surface-tint": "#a4c9ff",
              "on-secondary-fixed": "#002114",
              "on-primary-container": "#003a6b",
            },
            fontFamily: {
              headline: ["Inter"],
              body: ["Inter"],
              label: ["Inter"],
              mono: ["JetBrains Mono"],
            },
            borderRadius: {
              DEFAULT: "0.125rem",
              lg: "0.25rem",
              xl: "0.5rem",
              full: "0.75rem",
            },
          },
        },
      };
    </script>
    <style>
      .material-symbols-outlined {
        font-variation-settings:
          "FILL" 0,
          "wght" 400,
          "GRAD" 0,
          "opsz" 24;
        vertical-align: middle;
      }
      body {
        background-color: #131313;
        color: #e5e2e1;
        overflow: hidden;
      }
      .terminal-cursor {
        display: inline-block;
        width: 8px;
        height: 18px;
        background-color: #45dfa4;
        animation: blink 1s step-end infinite;
      }
      @keyframes blink {
        50% {
          opacity: 0;
        }
      }
      .running-glow {
        box-shadow: 0 0 15px rgba(96, 165, 250, 0.2);
      }
      .custom-scrollbar::-webkit-scrollbar {
        width: 4px;
      }
      .custom-scrollbar::-webkit-scrollbar-track {
        background: #0e0e0e;
      }
      .custom-scrollbar::-webkit-scrollbar-thumb {
        background: #2a2a2a;
      }
    </style>
  </head>
  <body
    class="font-body selection:bg-primary-container selection:text-on-primary-container"
  >
    <!-- SideNavBar (280px Fixed) -->
    <aside
      class="w-[280px] h-screen fixed left-0 top-0 bg-[#0E0E0E] flex flex-col py-8 z-50"
    >
      <div class="px-6 mb-10">
        <div class="flex items-center gap-3 mb-8">
          <div
            class="w-8 h-8 bg-primary rounded flex items-center justify-center"
          >
            <span
              class="material-symbols-outlined text-on-primary"
              style="font-variation-settings: 'FILL' 1;"
              >bolt</span
            >
          </div>
          <div>
            <h1 class="text-xl font-bold tracking-tighter text-[#E5E2E1]">
              Kinetic AI
            </h1>
            <p
              class="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant opacity-50"
            >
              v2.0.4-stable
            </p>
          </div>
        </div>
        <button
          class="w-full bg-primary hover:bg-primary-container text-on-primary font-bold py-2.5 px-4 rounded-md flex items-center justify-center gap-2 transition-all duration-200 active:scale-[0.98]"
          onclick="toggleModal(true)"
        >
          <span class="material-symbols-outlined text-[20px]">add</span>
          <span class="text-sm font-Inter tracking-tighter">+ Add Task</span>
        </button>
      </div>
      <nav class="flex-1 overflow-y-auto custom-scrollbar px-2 space-y-1">
        <div class="px-4 py-2 mb-2">
          <span
            class="text-[10px] font-mono uppercase tracking-widest text-on-surface-variant opacity-60"
            >Task Queue</span
          >
        </div>
        <!-- Active Task -->
        <div
          class="relative group cursor-pointer transition-all duration-200 pl-4 border-l-2 border-[#45DFA4] bg-transparent py-3 mb-4 running-glow"
        >
          <div class="flex flex-col gap-1">
            <div class="flex items-center justify-between pr-4">
              <span class="text-xs font-mono text-secondary">RUNNING</span>
              <span
                class="material-symbols-outlined text-[14px] text-secondary animate-pulse"
                style="font-variation-settings: 'FILL' 1;"
                >circle</span
              >
            </div>
            <span class="text-sm font-medium text-[#E5E2E1] truncate"
              >Refactor auth middleware</span
            >
            <span
              class="text-[11px] font-mono text-on-surface-variant opacity-60 truncate"
              >/workspaces/kinetic-core</span
            >
          </div>
        </div>
        <!-- Pending Tasks -->
        <div
          class="pl-4 opacity-60 hover:opacity-100 transition-all duration-200 py-3 group cursor-pointer hover:bg-[#2A2A2A] rounded-r-lg"
        >
          <div class="flex flex-col gap-1">
            <div class="flex items-center justify-between pr-4">
              <span
                class="text-[10px] font-mono uppercase text-on-surface-variant"
                >Pending</span
              >
            </div>
            <span class="text-sm font-Inter text-[#C1C7D3] truncate"
              >Generate unit tests for API</span
            >
            <span
              class="text-[11px] font-mono text-on-surface-variant opacity-40 truncate"
              >/workspaces/kinetic-core/api</span
            >
          </div>
        </div>
        <div
          class="pl-4 opacity-60 hover:opacity-100 transition-all duration-200 py-3 group cursor-pointer hover:bg-[#2A2A2A] rounded-r-lg"
        >
          <div class="flex flex-col gap-1">
            <div class="flex items-center justify-between pr-4">
              <span
                class="text-[10px] font-mono uppercase text-on-surface-variant"
                >Pending</span
              >
            </div>
            <span class="text-sm font-Inter text-[#C1C7D3] truncate"
              >Update documentation v2</span
            >
            <span
              class="text-[11px] font-mono text-on-surface-variant opacity-40 truncate"
              >/docs/main</span
            >
          </div>
        </div>
      </nav>
      <!-- Bottom Nav Links -->
      <div class="px-2 mt-auto pt-8 border-t border-outline-variant/10">
        <div
          class="pl-4 py-2 opacity-60 hover:opacity-100 hover:bg-[#2A2A2A] rounded-lg transition-all cursor-pointer flex items-center gap-3"
        >
          <span class="material-symbols-outlined text-sm">dashboard</span>
          <span class="text-sm font-Inter tracking-tighter">Dashboard</span>
        </div>
        <div
          class="pl-4 py-2 opacity-60 hover:opacity-100 hover:bg-[#2A2A2A] rounded-lg transition-all cursor-pointer flex items-center gap-3"
        >
          <span class="material-symbols-outlined text-sm">model_training</span>
          <span class="text-sm font-Inter tracking-tighter">Model Library</span>
        </div>
        <div
          class="pl-4 py-2 opacity-60 hover:opacity-100 hover:bg-[#2A2A2A] rounded-lg transition-all cursor-pointer flex items-center gap-3"
        >
          <span class="material-symbols-outlined text-sm">settings</span>
          <span class="text-sm font-Inter tracking-tighter">Settings</span>
        </div>
      </div>
    </aside>
    <!-- Main Content Area -->
    <main class="ml-[280px] min-h-screen flex flex-col relative bg-surface">
      <!-- TopNavBar -->
      <header
        class="h-16 bg-[#131313]/80 backdrop-blur-xl flex justify-between items-center px-12 z-40 border-b border-white/5"
      >
        <div class="flex items-center gap-8">
          <div class="flex flex-col">
            <span
              class="text-[9px] font-mono uppercase tracking-widest text-on-surface-variant opacity-50"
              >Current Context</span
            >
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-[14px] text-primary"
                >folder_open</span
              >
              <span class="text-xs font-mono text-primary-fixed"
                >/workspaces/kinetic-core</span
              >
            </div>
          </div>
          <div class="h-8 w-[1px] bg-outline-variant/20"></div>
          <div class="flex flex-col">
            <span
              class="text-[9px] font-mono uppercase tracking-widest text-on-surface-variant opacity-50"
              >Active Prompt</span
            >
            <span
              class="text-xs font-Inter text-on-surface truncate max-w-[300px]"
              >Refactor auth middleware to use JWT with asymmetric keys...</span
            >
          </div>
        </div>
        <div class="flex items-center gap-4">
          <div class="flex gap-4 mr-6">
            <span
              class="text-[10px] font-mono uppercase tracking-widest text-[#60A5FA] font-bold border-b border-[#60A5FA] cursor-pointer"
              >Logs</span
            >
            <span
              class="text-[10px] font-mono uppercase tracking-widest text-[#C1C7D3] hover:text-[#E5E2E1] cursor-pointer transition-colors"
              >Artifacts</span
            >
            <span
              class="text-[10px] font-mono uppercase tracking-widest text-[#C1C7D3] hover:text-[#E5E2E1] cursor-pointer transition-colors"
              >Output</span
            >
          </div>
          <button
            class="bg-error-container hover:bg-error text-on-error-container font-mono text-[10px] uppercase tracking-widest py-2 px-4 rounded border border-error/20 transition-all duration-200 flex items-center gap-2 active:scale-95"
          >
            <span class="material-symbols-outlined text-sm">stop_circle</span>
            Interrupt (Ctrl+C)
          </button>
        </div>
      </header>
      <!-- Terminal Workspace -->
      <section
        class="flex-1 bg-black p-6 font-mono text-sm leading-relaxed overflow-hidden flex flex-col"
      >
        <div
          class="w-full h-full text-secondary-fixed-dim/90 overflow-hidden"
          id="terminal"
        ></div>
      </section>
    </main>
    <!-- Task Entry Modal (Hidden by default) -->
    <div
      class="fixed inset-0 z-[100] hidden items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      id="taskModal"
    >
      <div
        class="w-full max-w-2xl bg-surface border border-outline-variant/20 rounded-xl shadow-[0_20px_50px_rgba(0,0,0,0.5)] overflow-hidden"
      >
        <div class="p-6">
          <div class="flex items-center justify-between mb-8">
            <div class="flex items-center gap-3">
              <span class="material-symbols-outlined text-secondary"
                >terminal</span
              >
              <h2 class="text-lg font-bold tracking-tight text-on-surface">
                Create New Task
              </h2>
            </div>
            <button
              class="text-on-surface-variant hover:text-on-surface transition-colors"
              onclick="toggleModal(false)"
            >
              <span class="material-symbols-outlined">close</span>
            </button>
          </div>
          <form id="taskForm" class="space-y-6">
            <div class="space-y-2">
              <label
                class="block text-[10px] font-mono uppercase tracking-widest text-on-surface-variant"
                >Working Directory (cwd)</label
              >
              <input
                id="cwdInput"
                name="cwd"
                class="w-full bg-surface-container-high border-none rounded-md focus:ring-1 focus:ring-secondary text-on-surface font-mono text-sm py-3 px-4 placeholder:opacity-30"
                placeholder="/workspaces/..."
                type="text"
                required
              />
            </div>
            <div class="space-y-2">
              <label
                class="block text-[10px] font-mono uppercase tracking-widest text-on-surface-variant"
                >Prompt (Task)</label
              >
              <textarea
                id="promptInput"
                name="prompt"
                class="w-full bg-surface-container-high border-none rounded-md focus:ring-1 focus:ring-secondary text-on-surface font-Inter text-sm py-3 px-4 placeholder:opacity-30 resize-none"
                placeholder="Describe the task you want the agent to perform..."
                rows="4"
                required
              ></textarea>
            </div>
            <div
              class="flex items-center justify-end gap-3 pt-4 border-t border-outline-variant/10"
            >
              <button
                class="px-6 py-2 text-sm font-medium text-on-surface-variant hover:text-on-surface transition-colors"
                onclick="toggleModal(false)"
                type="button"
              >
                Cancel
              </button>
              <button
                class="bg-primary text-on-primary font-bold py-2 px-8 rounded-md transition-all active:scale-95 shadow-lg shadow-primary/10"
                type="submit"
              >
                Execute Task
              </button>
            </div>
          </form>
        </div>
        <div
          class="bg-surface-container-low px-6 py-3 flex items-center justify-between"
        >
          <span class="text-[10px] font-mono text-on-surface-variant opacity-40"
            >Press
            <kbd class="bg-surface-container-high px-1 rounded">ESC</kbd> to
            close</span
          >
          <div class="flex gap-4">
            <span class="text-[10px] font-mono text-secondary opacity-60"
              >Mode: Autonomous</span
            >
            <span class="text-[10px] font-mono text-primary opacity-60"
              >Model: kinetic-gpt-4-v2</span
            >
          </div>
        </div>
      </div>
    </div>
    <!-- UI Logic Script -->
    <script>
      const modal = document.getElementById("taskModal");

      function toggleModal(show) {
        if (show) {
          modal.classList.remove("hidden");
          modal.classList.add("flex");
        } else {
          modal.classList.add("hidden");
          modal.classList.remove("flex");
        }
      }

      // Close on escape
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          toggleModal(false);
        }
        // Cmd/Ctrl + K to open
        if ((e.metaKey || e.ctrlKey) && e.key === "k") {
          e.preventDefault();
          toggleModal(true);
        }
      });

      // Close on background click
      modal.addEventListener("click", (e) => {
        if (e.target === modal) {
          toggleModal(false);
        }
      });
    </script>
  </body>
</html>
```
