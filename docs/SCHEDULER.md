# Scheduler — reminders and actions (FLAI v13.5)

FLAI answers when asked. This makes it act **at a time**: "remind me tomorrow at nine",
"every Friday at 16:00 check the logs" — one of two kinds, created from ordinary chat.

## Two kinds, and why they are apart

| | Reminder | Action |
|---|---|---|
| What it is | a sentence prepared when it was created | work that plans and runs by itself |
| Costs | nothing at delivery — one database row and an event | a task on the Tasks engine, GPU time, a place in the queue |
| Can use tools | no need | everything the user's chat can: search, documents, files, cameras, network tools, skills |
| Arrives in | 🔔 Scheduler: reminders | 🛠️ Scheduler: actions |
| Also | a popup on screen | only the message |

Collapsing the two would either deny an action its tools or make every reminder a GPU
request. Keeping them apart is why a reminder is reliable and an action is honest about
being late.

## Creating one

Ask in chat. Two tools carry the request, and the router has two doors for it: the request
kernel's `schedule` intent, and category 13 `[-SCHEDULE-]` for the marker router
(`KERNEL_ENABLED=0`). Both send the request to ordinary chat, where the tools live — a
request that names a time is neither a web search nor reasoning, and both of those paths
offer no tools at all.

```
"remind me tomorrow at nine about the report"   → schedule_reminder
"every Friday at 16:00 check the logs"          → schedule_action
"remind me about the oven in fifteen minutes"   → schedule_reminder
```

The model works out **when** from the words and hands over a local wall-clock moment; the
server checks it, stores it and answers with the concrete time — "🔔 Reminder set for 18
November, 09:00". That answer is the only chance to notice a misunderstanding before it
fires, which is why it always names the stored moment.

| Situation | What happens |
|---|---|
| A one-off in the past | refused with `⚠️` and a question — nothing is created |
| A repeating phrase whose time already passed today | moves to its next occurrence: "every day at nine" said at ten means tomorrow |
| A phrase without a time ("tomorrow morning") | refused with a question, never a guess |
| An empty reminder or goal | refused |
| Text over 4 000 characters | refused; a schedule is a sentence, not a document |

## Recurrence

A closed set, computed on the **wall clock of the installation's zone**:

| Rule | Behaviour |
|---|---|
| `once` | fires once; the row stays in the list as finished |
| `minutes` | every N minutes; the minimum is 15, because anything faster is a busy loop wearing a schedule's clothes |
| `daily` | the same local time every day |
| `weekly` | the same local time on the chosen weekday |
| `monthly` | the same local time on the chosen day; a month without it (the 31st in February) fires on its last day rather than skipping |

Nine in the morning stays nine across a daylight-saving change: the next firing is computed
from the item's own zone, not by adding 24 hours. The zone is a **snapshot** of the
installation's `TIMEZONE` taken when the item was created — FLAI has one zone per
installation, and a per-user zone later becomes a new value in this column, not a migration.

## The two chats

Each user has one chat per kind, created on first delivery, titled in their language:

- **🔔 Scheduler: reminders** — the delivered sentences;
- **🛠️ Scheduler: actions** — the results of the work.

They are ordinary sessions: they can be renamed, exported and deleted. A deleted chat is
recreated on the next delivery, so a reminder is never lost because the chat was removed.

## The popup

A reminder also appears as a dialog in the centre of the screen with one Close button. It
is only a notification: the message is already stored in its chat, so closing it loses
nothing. A second reminder waits until the first is closed instead of replacing it. An
action never pops up — its result is a message, not an interruption.

## Missed firings

A firing more than `SCHEDULER_GRACE_MIN` (default 2 minutes) late counts as **missed**, and
a per-kind policy decides what the person hears:

| Policy | Reminder | Action |
|---|---|---|
| `all` | every missed firing, each with its own time | one — the newest |
| `last` (default) | only the newest, preceded by "Skipped firings: N" | one — the newest |
| `none` | nothing; the item advances to its next future firing | nothing; same |

`all` is bounded by `SCHEDULER_CATCHUP_MAX` (default 20): an every-fifteen-minutes item
after a week of downtime is 672 firings, and past the cap the item behaves as `last` and
says how many it stands for. An action always catches up **once**: four copies of the same
work is a pile behind one GPU, not four results.

## An action is a run, never a task

A firing puts one **run** in its own table (`scheduler_runs`), not a task on the Tasks engine.
A run is a short service loop, so it cannot appear in the Tasks tab — by construction rather
than by a filter a later query could forget — and it costs a few model calls instead of a
whole plan.

- **Queued one minute ahead** (`SCHEDULER_ACTION_LEAD_MIN`, default 1): a run takes seconds,
  so the answer is ready at the target instead of arriving late by design.
- **Overlap is refused, not queued.** While the previous run of the same item is queued or
  running, the new firing is skipped; a pile behind one GPU is not a schedule.
- **The answer waits for its time.** A run finished early is delivered at its target; one
  finished late says so — "Finished at 12:14 instead of 12:00".
- **A run that never started is cancelled** after `SCHEDULER_MAX_LATENESS_MIN` (default 60)
  and delivers "⚠️ The action could not run in time", so yesterday's rate is never read today.
- **Chat outranks a run but does not cancel it.** Chat takes the queue ahead of it; a run
  already executing finishes its few seconds. Only deleting the item cancels a run.
- **A restart cannot wedge an item.** Every run left `running` is cancelled at startup, and
  the overlap rule does not keep reading it.

### When the work is larger than a run

A run carries `start_task(goal, title)`. When the goal says to start a task, the model calls
it — there is no phrase matching in code — and the server creates an ordinary task titled
`🛠️ Scheduler: <title>` at the user's class. That task appears in the Tasks tab like any
other, its result stays there, and the run's own message is the tool's confirmation
("🛠️ Снимки комнат — the task is started and its result will appear in Tasks"), verbatim.
One run launches at most one task.

A run does **not** get the skill loader or the scheduling tools: a schedule must not create
schedules, and there is no turn in which a loaded skill body would be read. Everything else
from the chat catalogue is available, including writing a document.

How a run ends is polled: one query returns the finished runs whose time has come, and the
tick delivers them. A run's `error` is what the person sees only through the delivered
message; the details stay in the row.

## The list

**Profile popup → Scheduled**: the kind, the title, the next run and one Delete. There is
no editor on purpose — changing a schedule is delete-and-recreate from chat, so there is one
creation path and the panel stays a list.

Deleting cancels whatever the item already started and silences its pending result: a
message from a schedule the user removed is what they would report as a bug.

## Settings

**Admin → Settings → Scheduler**:

| Key | Meaning | Default |
|---|---|---|
| `SCHEDULER_ENABLED` | deliver reminders and actions at all | `1` |
| `SCHEDULER_MISSED_REMINDER` | all / last / none for reminders | `last` |
| `SCHEDULER_MISSED_ACTION` | all / last / none for actions | `last` |
| `SCHEDULER_CATCHUP_MAX` | the most firings one catch-up delivers | `20` |
| `SCHEDULER_ACTION_LEAD_MIN` | minutes before its target a run is queued | `1` |
| `SCHEDULER_RUN_ROUNDS` | model rounds one run may spend | `3` |
| `SCHEDULER_RUN_TIMEOUT_S` | seconds one run may take; checked before each round | `180` |
| `SCHEDULER_MAX_LATENESS_MIN` | minutes after its target a task that never started is cancelled | `60` |
| `SCHEDULER_GRACE_MIN` | minutes late before a firing counts as missed | `2` |

The two policies are enum-validated: a typo cannot silently become "no reminders at all".
All of them are read on every tick, so a change applies without a restart.

## Honest limits

- **A message at 03:00 is not an alert.** An action's result is a message; nothing wakes the
  user. A "notify" flag that reuses the reminder popup is a later release.
- **The time is a target, not a guarantee.** An action is a queued task behind chat; a deep
  queue or a long conversation makes it late, and it says so.
- **One zone.** The installation's zone is snapshotted per item; there is no per-user zone
  yet, because the rest of the product does not keep one either.
- **No arbitrary recurrence.** The set is closed (once, minutes, daily, weekly, monthly);
  there is no RRULE and no calendar integration.
- **A reminder needs no model.** That is deliberate — a hundred can fire in one second — but
  it also means a reminder says exactly what was written when it was created.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Nothing is delivered | `SCHEDULER_ENABLED=false`, or the tick thread did not start: look for «scheduler: tick thread started» in the log |
| A phrase is answered as a search or a reasoning task | the router did not recognise it as scheduling: `KERNEL_ENABLED=1` uses the `schedule` intent, the marker router uses category 13 `[-SCHEDULE-]` |
| A reminder arrives twice in one minute | two workers without the Redis lease — they must share the same Redis |
| An action is always "could not run in time" | the queue is deeper than `SCHEDULER_MAX_LATENESS_MIN`; raise it or lower the frequency |
| The list is empty but a reminder fired | the chat was deleted: delivery recreates it, the list shows items, not messages |

## Related

- [TASKS.md](TASKS.md) — the engine an action runs on
- [CONFIGURATION.md](CONFIGURATION.md) — the environment behind the panel keys
- [ARCHITECTURE.md](ARCHITECTURE.md) — where the tick sits in the process
