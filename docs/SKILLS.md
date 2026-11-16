# Skills — FLAI v13.5

A skill is a folder of instructions the model can load **on demand**. The catalogue
line — name and one description — sits in every system prompt; the body is read only
when the model decides it needs it, and it arrives as a tool result. That is what makes
a skill affordable: a well-written one costs one line of context until it is used.

The format is the one Claude Code, opencode and pi use, so a skill written for any of
them works here, and one written here works there.

## The file format

```
skills/
└── web-research/
    ├── SKILL.md        # English, required
    └── SKILL.ru.md     # Russian, optional
```

```markdown
---
name: web-research
description: Gather and cross-check sources before answering a factual question.
allowed-tools: web_search, read_page
---
# Web research

1. Run one web_search with the question's key terms.
2. Open the two most relevant results with read_page.
```

Three keys are understood:

| Key | Rule |
|---|---|
| `name` | `^[a-z0-9][a-z0-9_-]{0,63}$`, and it must equal the folder name |
| `description` | non-empty, at most 1024 characters — this is the catalogue line |
| `allowed-tools` | comma-separated tool names, checked when the skill is saved |

The folder name **is** the name: that is what makes a duplicate impossible, and why
the panel refuses a name another skill already uses.

A localized variant (`SKILL.ru.md`) carries its own frontmatter, because the scanner
reads every variant through the same validator. The panel writes that frontmatter for
you: you supply the Russian description and body, and the name is copied from the
English document.

## Two storage zones

| Zone | Path | In git | In the panel |
|---|---|---|---|
| Bundled | `skills/` | yes | listed, can be disabled, **cannot be edited or deleted** |
| Installed | `data/skills/` | no | upload, create, edit, enable, disable, delete |

Bundled skills ship with the project. Installed ones are written by the panel into the
git-ignored `data/` tree, so installing a skill never dirties the working tree and
never travels into a release by accident.

## Installing from the panel

**Admin → Skills**, immediately after **Models**:

- **Upload .md** — one file, at most 64 KB, UTF-8. The name comes from its frontmatter.
- **New skill** — the same editor, empty.
- **Edit** — the English document as raw text with its frontmatter, plus the Russian
  description and body as two separate fields.

Every save runs the same validator the scanner runs, so the panel cannot write a skill
the scanner would later ignore. A save is live on the next message: the scan cache is
dropped rather than waited out.

## How a skill reaches the model

1. **Catalogue** — `catalog()` renders `- \`name\`: description` into the system prompt
   (`{skill_catalog}` in `chat.template`).
2. **Selection** — the request kernel names the matching skill in the same JSON decision
   where it picks the intent (at most two), and only from names the catalogue lists.
3. **Load** — `RedisRequestQueue._preload_skills()` appends those bodies to the system
   prompt for that request. Not as a synthetic tool exchange: measured, a body presented
   as "a tool already ran" makes the model answer from memory instead of using it.
4. **Fallback** — a skill the model calls directly through `activate_skill` returns its
   body as a tool result. That tool is advertised only while at least one skill exists.

A skill never overrides a pipeline intent: crawl, document search, history, memory and
media actions are decided before any skill is considered.

## What the tool marks mean

`allowed-tools` is a contract, checked when you save. Each name lands in one of four
classes, and the panel shows which:

| Mark | Meaning | Blocks the save |
|---|---|---|
| (plain) | the tool is in the catalogue right now | no |
| *Здесь недоступен* | a real tool whose service is not running (`read_page` without the crawler, `list_rooms` without the camera) | no |
| *Неизвестный инструмент* | one keystroke away from a real name — `web-serach` for `web_search` | **yes**, with the suggestion |
| *Здесь недоступен* (foreign) | a name this installation never had — `Read`, `Bash(git:*)` | no |

The fourth class is deliberate: skills written for Claude Code list *its* tools, and
rejecting a file over a name that is correct elsewhere would refuse exactly the files
the upload button exists for.

The field is **not** a sandbox: FLAI does not narrow the tool list to what a skill names.
A skill that says "use only `web_search`" is telling the model what it means, not
restricting what it can do.

## Disabling, removing and shadowing

- **Disable** removes a skill from the catalogue, from `activate_skill`'s enum and from
  the set of names the kernel may return. The file stays. The flag lives in
  `skill_settings` in PostgreSQL, not in the frontmatter — an editor cannot change
  behaviour behind the panel's back.
- **Delete** removes an installed folder and its files. A bundled skill offers no delete.
- **Shadowing** happens when a release ships a bundled skill whose name you already
  installed. The bundled one wins and appears once; the installed copy stays on disk,
  listed as *перекрыт встроенным навыком* with a **Rename** action. Renaming saves the
  document under the new name and removes the old folder only after that save succeeded.

## Troubleshooting a skill that never fires

| Symptom | Check |
|---|---|
| The skill is not in the prompt | is it enabled? `flask` has no command for this — the panel's *Enabled* column is the answer |
| The model never picks it | the description is what the model chooses from: name the situation, not the technique |
| `activate_skill` is missing | no skill is enabled, so the tool is not advertised |
| The body loaded but the answer ignores it | the body reads as advice, not as steps — write numbered instructions with tool names |
| A Russian variant is not used | its frontmatter is missing or its `name` differs; `SKILL.<lang>.md` without valid frontmatter is logged and skipped |
| Saving is refused with a tool name | that name is a typo; the message names the tool you meant |

## Related

- [ARCHITECTURE.md](ARCHITECTURE.md) — where skills sit in a request
- [TASKS.md](TASKS.md) — the background engine's own capability list, which is a
  different catalogue
