# Background Tasks (Задачи)

**For what?** Some jobs are too big for one chat request: prepare a 10-page report from several sources, research a topic across a dozen web pages and draft a structured memo, generate a set of images and describe them. **Tasks** are long-running background jobs: you describe the result, the system decomposes the request into a plan of steps and executes them one by one at **lower priority than chat** — a task never makes your chat request wait, and any chat request freezes the running task and takes the resources back.

## How to use it

1. Open the **Задачи / Tasks** tab in the left column (between **Documents** and **Sessions**), press ➕ and describe the desired result.
2. The task is planned and starts running. Its conversation opens in the main window and looks like a normal chat: the user request, a «Plan of N steps:» message, then each step's output prefixed with «Шаг N из M:» (Step N of M), and the final answer.
3. Live status is shown on each list item: ⏳ queued / ▶ Running (step n of m) / ⏸ Frozen / ⏳ Awaiting answer / ✔ Completed / ✖ Failed / ✖ Cancelled / ⏰ Expired.
4. When the model asks a clarifying question, the task shows «Ожидает ответа» (Awaiting answer) and the message input becomes the answer box («Ответьте на уточнение…»). Answer and the task continues.
5. ■ (Stop task) cancels a running or waiting task; 🗑️ deletes it (a non-terminal task is cancelled first, then removed with its whole conversation).

## Priorities and preemption

- A task's priority class is derived from the user's service class at creation: `0 → 3`, `1 → 4`, `2 → 5`. Classes 3/4/5 are **internal** and never offered in the admin panel — users keep classes 0/1/2.
- **One task is active globally** (selection by class 3 before 5, then FIFO). Awaiting user input or being frozen releases the slot for the next task.
- **Quiet window (sliding):** after the last chat request, a task may start/resume only when `TASK_QUIET_PERIOD_S` (default 60 s) passed with no new chat requests, chat queues are empty and the GPU is free. Any new chat request resets the window and **aborts the in-flight step**: its partial output is discarded, the step is marked `interrupted` (reason `chat`) and re-executed from scratch on resume. Chat never waits for a task.
- **Budget:** a task may accumulate at most `TASK_TOTAL_BUDGET_S` (default 24 h) of **pure execution** — waits, freezes and awaiting-user time never count. On expiry the task is `expired` and a final summary is generated from the intermediate results.

## What happens inside

1. **Planning** — a reasoning-model call decomposes the request into steps (title, goal, one capability per step, a verifiable `checkpoint_criteria` each). Missing information becomes a clarifying question step.
2. **Steps** — each step runs one capability from the registry (`app/tasks/tools.py`):

| Tool | What it does |
|------|--------------|
| `llm` | reasoning-model call with the accumulated intermediate results (multimodal only when a step must inspect earlier produced images) |
| `search` | web search (Tavily → SearXNG fallback) |
| `calculate` | the safe AST-based calculator |
| `rlm` | Deep Analysis run as a queue sub-task |
| `image_gen` / `image_edit` / `video_gen` | media sub-tasks through the existing GPU queue |

3. **Checkpoint** — a short reasoning call verifies the step result against `checkpoint_criteria`; on failure the step reruns with the mismatch description (up to `TASK_STEP_MAX_ATTEMPTS` = 3 total), then is marked `failed` and the task continues.
4. **Final** — the answer/action over the accumulated results, with media references; the task becomes `completed`.

Media sub-tasks run in the existing queue at the task's class (3/4/5). A slow-worker guard keeps them from starting while the gate is closed — they wait in a deferred list and return to the queue when chat goes quiet.

## Persistence and recovery

`tasks` + `task_steps` (PostgreSQL) persist the whole state; the executor **resumes after a restart** — an interrupted step gets a fresh attempt with reason `restart`. Only each attempt's real runtime counts toward the budget, so restarts never eat the 24 h budget. The conversation is rendered from `task_steps` — there is no parallel messages table. Both tables are included in full backups.

## Notes

- Task media (generated images/videos) live in the regular upload folder, referenced from step `extra`; deleting a task deletes its conversation (steps are removed with it).
- Tasks updates arrive on the same per-user SSE stream as chat events (`task_update` type) — the list and the open conversation refresh without polling.
- The internal API lives at `/api/tasks*` (session-auth, owner-scoped like sessions); there is **no public `/v1` task API** in this version.
- The capabilities registry is the seam for a future **skills** concept: a skill would be a named capability bundle appearing in the same registry without storage changes.

## Configuration

All settings with defaults: `docs/CONFIGURATION.md` («Background Tasks»). Kill switch `TASKS_ENABLED=0` stops the executor from picking up new tasks.
