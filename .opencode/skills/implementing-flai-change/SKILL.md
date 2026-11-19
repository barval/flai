---
name: implementing-flai-change
description: "Use when implementing a feature, fix or refactor in FLAI that is larger than a one-line edit — designing it, writing the plan, or executing a plan task by task with subagents."
---

# Implementing a FLAI Change

## What this skill is

A thin overlay on the superpowers process. The authoritative procedure is
`superpowers:brainstorming`, `superpowers:writing-plans` and
`superpowers:subagent-driven-development`; this file adds only the rules that
are specific to FLAI.

## Size the work first

| Size | Example | Path |
|------|---------|------|
| Quick task | a typo, one string, one constant | do it, no plan |
| Small change | one file, one behaviour | a chat-level plan, then implement |
| Project | several files, schema or API impact | design + plan + subagent execution |

If the work grows mid-task, stop and step up a size.

## Artefacts

| Artefact | Path | In git |
|----------|------|--------|
| Design | `docs/superpowers/specs/` | no |
| Plan | `docs/superpowers/plans/` | no |
| Ledger | `.superpowers/sdd/` | no |

All three are git-ignored by project decision. Never `git add -f` them.

## Design

Use the brainstorming skill. Write the design in English, but do not copy real
user quotes, account names, hosts or IP addresses into it. The design answers
why, goals, non-goals, constraints, and the parts of the how the user decided.

## Plan

Use the writing-plans skill. Every task must:

- name the files it touches;
- carry its own test (one file, or a `::`/`-k` selection);
- end in a runnable state;
- say whether it commits.

Plan approval means the user approved exactly those files and those commits.
Anything outside the plan stops and asks.

## Execute

Use subagent-driven-development with these FLAI overrides:

- No branch and no worktree without explicit approval. The development stack is
  bind-mounted to this checkout, so a worktree has no services and no data.
- A subagent implements and edits; the lead runs every pytest command. A
  subagent that needs a test result asks the lead for it.
- No subagent runs docker commands, database commands, or anything against a
  running service.
- If the permission layer asks on a subagent commit, the lead performs the
  commit.
- A ruling goes into the ledger, not into a question to the user. Stop only for
  an irreversible action, a security-sensitive action, a merge/push/tag, or a
  plan where every path is a guess.

## Test policy

The RAM ban in `AGENTS.md` applies to every agent, including subagents.

Before any pytest run, confirm all three:

1. `pgrep -c pytest` prints `0`;
2. `free -h` shows at least 4 GiB available;
3. the command selects one file, or a `::`/`-k` slice, and you can state the
   collected test count.

Record the exact command and the summary line in the ledger. Never run the full
suite, `pytest -m unit` wholesale, load tests or benchmarks as a routine check.

## Ledger

One file per plan: `.superpowers/sdd/<date>-<topic>/progress.md`. Record:

- setup: branch, base commit, whether a worktree was used;
- pre-flight: task order and dependencies;
- per task: complete/incomplete, commit range, exact test command and result;
- every decision as `Ruling: <what> — <why> — cost if wrong: <cost>`;
- final review findings and the merge commit.

## Handoff

- Release work -> `preparing-flai-release`.
- A server -> `deploying-flai`.
- A new release line -> `opening-flai-release`.

## What not to do

- Do not copy the superpowers procedure here; it will drift.
- Do not run the ceremony for a quick task.
- Do not treat plan approval as approval for files outside the plan.
- Do not commit, push, tag, merge or revert outside an approved plan.
