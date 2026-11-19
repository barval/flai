#!/usr/bin/env python3
"""The READMEs must describe the product that ships, not the product of two releases ago.

`README.md` is what a person reads before installing, and it drifts in a way no other gate
sees: a feature is finished, documented in `docs/`, and never appears in the README at all.
Measured at v13.6: the sections named ten capabilities while the build shipped a scheduler,
network diagnostics, assistant-written files, document sharing and panel-installed skills —
and the router had fourteen categories while the README still said eleven.

This gate checks four things, all derived from code or from the repository itself:

* every document in `docs/` is linked from the README of its language (an internal audit
  document may be excluded with a reason);
* every router marker in `prompts/en/base_text.template` appears in both READMEs;
* the number of categories named in the TL;DR matches the highest category number;
* a fixed list of user-facing capabilities is named in both languages.

It fails on an omission, not on wording: a phrase may be reworded, but the capability has
to stay mentioned somewhere in the file.

Exit codes:
    0  the READMEs describe the product
    1  findings
    2  a file this gate needs is missing or unreadable
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Documents that are not user-facing capabilities: an audit for maintainers.
DOC_EXCLUSIONS = {
    "UPGRADES.md": "dependency and image audit for maintainers",
    "UPGRADES-ru.md": "dependency and image audit for maintainers",
}

# The same phrase requirement as the capability list, but for the public description.
CAPABILITIES: dict[str, tuple[str, str]] = {
    "chat": ("intelligent chat", "интеллектуальный чат"),
    "reasoning": ("reasoning model", "модель рассуждений"),
    "tools": ("native tool calling", "нативный вызов инструментов"),
    "skills": ("admin → skills", "админ-панель → навыки"),
    "mcp": ("mcp", "mcp"),
    "deep_analysis": ("deep analysis", "глубокий анализ"),
    "network": ("network diagnostics", "сетевая диагностика"),
    "scheduler": ("reminders", "напоминани"),
    "tasks": ("background tasks", "фоновые задачи"),
    "image_gen": ("image generation", "генерация изображений"),
    "image_edit": ("image editing", "редактирование изображений"),
    "video": ("video generation", "генерация видео"),
    "web_search": ("web search", "веб-поиск"),
    "rag": ("document search", "поиск по документам"),
    "folders": ("document folders", "папки документов"),
    "sharing": ("document sharing", "общий доступ к документам"),
    "written_files": ("files the assistant writes", "файлы, которые пишет ассистент"),
    "crawl": ("deep study", "глубокое изучение"),
    "graph": ("knowledge graph", "граф знаний"),
    "memory": ("long-term memory", "долговременная память"),
    "voice": ("whisper", "whisper"),
    "tts": ("piper", "piper"),
    "camera": ("camera", "камер"),
    "api": ("openai-compatible", "openai-совместимый"),
    "backups": ("backups", "резервные копии"),
    "monitoring": ("monitoring", "мониторинг"),
    "universal_mode": ("one model or two", "одна модель или две"),
}

NUMBER_WORDS: dict[int, tuple[str, str]] = {
    9: ("nine", "девяти"),
    10: ("ten", "десяти"),
    11: ("eleven", "одиннадцати"),
    12: ("twelve", "двенадцати"),
    13: ("thirteen", "тринадцати"),
    14: ("fourteen", "четырнадцати"),
    15: ("fifteen", "пятнадцати"),
    16: ("sixteen", "шестнадцати"),
}

LINK = re.compile(r"\]\((docs/[A-Za-z0-9_./-]+\.md)\)")
MARKER = re.compile(r"\[-([A-Z][A-Z-]*[A-Z])-\]")
CATEGORY = re.compile(r"^## (\d+)\.", re.M)


def _docs_linked(readme: Path) -> set[str]:
    return set(LINK.findall(readme.read_text(encoding="utf-8")))


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    template = root / "prompts" / "en" / "base_text.template"
    readmes = {"en": root / "README.md", "ru": root / "README-ru.md"}
    if not all(path.exists() for path in readmes.values()) or not template.exists():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    findings: list[str] = []

    for lang, suffix in (("en", ""), ("ru", "-ru")):
        text = readmes[lang].read_text(encoding="utf-8")
        linked = _docs_linked(readmes[lang])
        for page in sorted((root / "docs").glob(f"*{suffix}.md")):
            if not suffix and page.name.endswith("-ru.md"):
                continue
            relative = f"docs/{page.name}"
            if relative in linked or page.name in DOC_EXCLUSIONS:
                continue
            findings.append(f"UNLINKED DOC   {relative} — not reachable from {readmes[lang].name}")

    template_text = template.read_text(encoding="utf-8")
    markers = MARKER.findall(template_text)
    categories = [int(number) for number in CATEGORY.findall(template_text)]
    if not markers or not categories:
        print("could not read the router categories from the template", file=sys.stderr)
        return 2
    highest = max(categories)

    for lang in ("en", "ru"):
        text = readmes[lang].read_text(encoding="utf-8")
        lower = text.lower()
        for marker in sorted(set(markers)):
            if f"[-{marker}-]" not in text:
                findings.append(f"MISSING MARKER {lang}: [-{marker}-] is not in {readmes[lang].name}")

        expected = NUMBER_WORDS.get(highest)
        pattern = r"one of ([\w-]+) categories" if lang == "en" else r"в одну из ([\w-]+) категорий"
        found = re.search(pattern, lower)
        if expected is None:
            findings.append(f"NO WORD        {lang}: no number word for {highest} categories")
        elif found is None:
            findings.append(f"NO COUNT       {lang}: the category-count sentence was not found")
        elif found.group(1) != expected[0 if lang == "en" else 1]:
            findings.append(
                f"WRONG COUNT    {lang}: the TL;DR says {found.group(1)!r},"
                f" the router has {highest} categories ({expected[0 if lang == 'en' else 1]!r})"
            )

        for capability, (english, russian) in sorted(CAPABILITIES.items()):
            needle = english if lang == "en" else russian
            if needle.lower() not in lower:
                findings.append(
                    f"MISSING {lang.upper():2}     {capability}: {needle!r} is not in {readmes[lang].name}"
                )

    for finding in findings:
        print(finding)
    print(
        f"\n{len(findings)} finding(s); {len(set(markers))} router marker(s), {highest} categories, "
        f"{len(CAPABILITIES)} capability phrase(s) per language"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
