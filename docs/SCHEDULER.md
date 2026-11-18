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

## An action is a task

A firing puts one task on the Tasks engine — class 3/4/5 from the user's service class
0/1/2 — with the item it belongs to and the moment its result is due.

- **Enqueued ahead by `SCHEDULER_ACTION_LEAD_MIN`** (default 10): the task has to queue, wait
  for the GPU and run, so starting it exactly on the minute would deliver everything late by
  design. The lead is a constant, not a prediction of the queue.
- **Overlap is refused, not queued.** While the previous run is queued, active, frozen (the
  normal state while the user chats) or waiting for an answer, the new firing counts as
  skipped. A task waiting for an answer is not finished, and a second one would ask the
  same question twice.
- **The result waits for its time.** A task finished early stays visible in the Tasks tab
  and is delivered at its target; one finished late says so — "Finished at 12:14 instead of
12:00".
- **Anything that never started is cancelled** after `SCHEDULER_MAX_LATENESS_MIN` (default
  60) through the ordinary cancel flag, and delivers "⚠️ The action could not run in time".
  Stale work must not hold the single task slot for a day.

Delivery is **polled, not pushed**: one query covers every ending the engine has —
completed, failed, expired, cancelled — instead of a hook on each path.

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
| `SCHEDULER_ACTION_LEAD_MIN` | minutes before its target an action is enqueued | `10` |
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
