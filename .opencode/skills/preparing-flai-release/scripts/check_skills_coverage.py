#!/usr/bin/env python3
"""Every capability the product has must be in the list the assistant describes itself with.

`prompts/{lang}/skills.txt` is injected as `{skills_section}`. The chat template tells the
model to answer "what can you do?" from it, and a capability the list does not name is one
the assistant neither mentions nor offers proactively. Measured: the list named ten things
while the build shipped page reading, crawling, network diagnostics, reminders, file writing,
deep analysis, background tasks and skills — all working, none of them in the sentence.

The inventory is derived from code, not from a second prose list:

* every tool in `_EXECUTOR_MAP` (`app/tools.py`) must be owned by a capability;
* every id in `TOOL_SPECS` (`app/tool_registry.py`) must be owned by one;
* every capability must still say something in **both** languages;
* both lists must hold the same number of bullets.

A capability added without a line fails; so does a line whose feature was removed.

Exit codes:
    0  the list covers the product
    1  findings
    2  a file this gate needs is missing or unreadable
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# The map mixes literal names with tool-name constants (`WRITE_DOCUMENT_TOOL`).
EXECUTOR_KEY = re.compile(r'^\s+(?:"([a-z0-9_]+)"|([A-Z][A-Z0-9_]*)):\s*lambda ctx', re.M)
CONSTANT = re.compile(r'^([A-Z][A-Z0-9_]*)\s*=\s*"([a-z0-9_]+)"', re.M)
TASK_ID = re.compile(r'^\s{8}id="([a-z_]+)",', re.M)
BULLET = re.compile(r"^  - ", re.M)

# Which capability a tool belongs to. A tool missing here is a capability nobody wrote down.
TOOL_OWNER: dict[str, str] = {
    "get_current_time": "time",
    "time_calc": "time",
    "calculator": "calculator",
    "web_search": "web",
    "read_page": "read_page",
    "rag_search": "rag",
    "history_search": "history",
    "camera_snapshot": "camera",
    "list_rooms": "camera",
    "activate_skill": "skills",
    "write_document": "files",
    "edit_document": "files",
    "restore_document_version": "files",
    "start_task": "scheduler",
    "schedule_reminder": "scheduler",
    "schedule_action": "scheduler",
    "net_dns": "net",
    "net_whois": "net",
    "net_ping": "net",
    "net_trace": "net",
    "net_ports": "net",
    "net_http": "net",
    "net_speed": "net",
}

# The same for a background-task capability.
TASK_OWNER: dict[str, str] = {
    "llm": "conversation",
    "search": "web",
    "document": "files",
    "chat_tool": "tasks",
    "research": "web",
    "rag": "rag",
    "history": "history",
    "vision": "vision",
    "camera": "camera",
    "crawl": "crawl",
    "calculate": "calculator",
    "rlm": "rlm",
    "continuity": "tasks",
    "image_gen": "image_gen",
    "image_edit": "image_edit",
    "video_gen": "video",
}

# A phrase that must survive in each language. Lower-case: the check is case-insensitive.
CAPABILITY_TEXT: dict[str, tuple[str, str]] = {
    "conversation": ("maintain conversations", "поддерживаю диалог"),
    "time": ("current time", "текущее время"),
    "calculator": ("calculator", "калькулятор"),
    "web": ("web search", "поиск в интернете"),
    "read_page": ("specific page", "конкретную страницу"),
    "crawl": ("study a site", "изучаю сайт"),
    "rag": ("search for information in uploaded documents", "ищу информацию в загруженных документах"),
    "history": ("chat history", "историю переписки"),
    "vision": ("analyze uploaded images", "анализирую загруженные изображения"),
    "image_gen": ("generate images", "генерирую изображения"),
    "image_edit": ("edit uploaded images", "редактирую загруженные изображения"),
    "video": ("create videos", "создаю видео"),
    "voice": ("voice messages", "голосовые сообщения"),
    "audio": ("audio files", "аудиофайлы"),
    "camera": ("surveillance cameras", "камер видеонаблюдения"),
    "net": ("dns", "dns"),
    "scheduler": ("reminders", "напоминания"),
    "files": ("create files", "создаю файлы"),
    "rlm": ("deep analysis", "глубокий анализ"),
    "tasks": ("background tasks", "фоновые задачи"),
    "memory": ("long-term memory", "долговременная память"),
    "skills": ("skills", "навык"),
}


def _block(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def executor_tools(root: Path) -> set[str]:
    text = (root / "app" / "tools.py").read_text(encoding="utf-8")
    constants = dict(CONSTANT.findall(text))
    tools = set()
    for literal, identifier in EXECUTOR_KEY.findall(_block(text, "_EXECUTOR_MAP", "\n}")):
        tools.add(literal or constants.get(identifier, identifier))
    return tools


def task_capabilities(root: Path) -> set[str]:
    text = (root / "app" / "tool_registry.py").read_text(encoding="utf-8")
    return set(TASK_ID.findall(_block(text, "TOOL_SPECS: tuple", "RLM_TOOL_SPECS")))


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    langs = {"en": root / "prompts" / "en" / "skills.txt", "ru": root / "prompts" / "ru" / "skills.txt"}
    if not all(path.exists() for path in langs.values()) or not (root / "app" / "tools.py").exists():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    tools = executor_tools(root)
    tasks = task_capabilities(root)
    findings: list[str] = []

    for tool in sorted(tools - set(TOOL_OWNER)):
        findings.append(f"UNOWNED TOOL   {tool} — no capability in this gate covers it")
    for capability in sorted(tasks - set(TASK_OWNER)):
        findings.append(f"UNOWNED TASK   {capability} — no capability in this gate covers it")

    owners = set(TOOL_OWNER.values()) | set(TASK_OWNER.values())
    for owner in sorted(owners - set(CAPABILITY_TEXT)):
        findings.append(f"NO TEXT        {owner} — a capability with no phrase to look for")

    texts = {lang: path.read_text(encoding="utf-8").lower() for lang, path in langs.items()}
    for capability, (english, russian) in sorted(CAPABILITY_TEXT.items()):
        if english.lower() not in texts["en"]:
            findings.append(f"MISSING EN     {capability}: {english!r} is not in prompts/en/skills.txt")
        if russian.lower() not in texts["ru"]:
            findings.append(f"MISSING RU     {capability}: {russian!r} is not in prompts/ru/skills.txt")

    counts = {lang: len(BULLET.findall((root / "prompts" / lang / "skills.txt").read_text(encoding="utf-8"))) for lang in langs}
    if counts["en"] != counts["ru"]:
        findings.append(f"ASYMMETRY      en has {counts['en']} bullet(s), ru has {counts['ru']}")

    for finding in findings:
        print(finding)
    print(
        f"\n{len(findings)} finding(s); {len(tools)} tool(s), {len(tasks)} task capability(ies), "
        f"{len(CAPABILITY_TEXT)} capability phrase(s), {counts['en']} bullet(s) per language"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
