# tests/test_crawl_ui.py
"""Behavioural checks for the crawler's UI stage labels and compose profile.

These tests were rewritten after review round 1: the previous version asserted
string presence and a regex that matched a *volume* record, so it passed while
`docker compose config` exited 1 (the crawler block sat under the top-level
`volumes:` key) and while the page counter rendered a literal `%s`.

What is asserted here:

* the crawler service is structurally a *service* (YAML parse, always runs)
  and a real `docker compose config` invocation exits 0 (skipped when the
  docker CLI or `.env` is unavailable);
* `events.js` reads the payload field the backend actually sends, and the
  rendered label contains the page number instead of a literal `%s`
  (node harness over the real `events.js`; skipped when node is unavailable,
  backed by an unconditional static check on the counter chain);
* the three stage labels resolve through the *real compiled* catalogs in both
  ru and en, with msgids byte-identical to the `chat.html` lookup strings;
* both deploy scripts initialise `WITH_CRAWLER` before any read — proven by
  executing the scripts' own prologue + read statements under `set -euo pipefail`
  (the pre-fix scripts exit 1 with an unbound-variable error).
"""

import gettext
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / "docker-compose.gpu.yml"
EVENTS_JS = ROOT / "app" / "static" / "js" / "events.js"
CHAT_HTML = ROOT / "app" / "templates" / "chat.html"
DEPLOY_SCRIPTS = ("deploy.sh", "deploy-ru.sh")

# stage -> (STAGE_LABEL_KEYS key, chat.html TRANSLATIONS key)
CRAWL_STAGES = {
    "crawl_start": "stage_crawl_start",
    "crawl_page": "stage_crawl_page",
    "crawl_indexing": "stage_crawl_indexing",
}


# ── helpers ────────────────────────────────────────────────────────────


def _catalog(lang: str) -> gettext.NullTranslations:
    """Load the COMPILED catalog, so a stale .mo fails the test too."""
    with (ROOT / "translations" / lang / "LC_MESSAGES" / "messages.mo").open("rb") as f:
        return gettext.GNUTranslations(f)


def _chat_html_sources() -> dict[str, str]:
    """Extract {'stage_crawl_*': '<english source string>'} from chat.html.

    Jinja resolves `_('...')` server-side, so chat.html carries one
    language-neutral entry per key; the ru/en split lives in the catalogs.
    """
    html = CHAT_HTML.read_text(encoding="utf-8")
    sources = {}
    for key in CRAWL_STAGES.values():
        m = re.search(rf"'{key}':\s*\{{\{{ _\('([^']*)'\)\|tojson\s*\}}\}}", html)
        assert m, f"{key} missing from chat.html TRANSLATIONS"
        sources[key] = m.group(1)
    return sources


def _node_stage_labels(translations: dict[str, str], payload: str) -> list[str]:
    """Run the real events.js in node and return the texts it renders.

    events.js is evaluated verbatim in a vm context with a stubbed DOM; the
    progress element's textContent is what the user actually sees.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available; behavioural JS check skipped")
    harness = f"""
import fs from 'fs';
import vm from 'vm';
const TEMPLATES = {json.dumps(translations)};
const created = [];
const fakeEl = () => ({{
    setAttribute() {{}}, getAttribute() {{ return null; }}, remove() {{}},
    querySelectorAll() {{ return []; }}, querySelector() {{ return null; }},
    className: '', textContent: '', dataset: {{}}, style: {{}},
    contains() {{ return true; }},
}});
const chatMessages = fakeEl();
chatMessages.appendChild = (el) => {{ created.push(el); }};
const W = {{}};
const ctx = vm.createContext({{
    console, setInterval: () => 0, clearInterval: () => {{}}, setTimeout: () => 0,
    Date, Math, JSON, W, window: W, globalThis: W,
    document: {{
        getElementById: (id) => (id === 'chat-messages' ? chatMessages : null),
        createElement: () => fakeEl(),
        body: {{ contains: () => true }},
        addEventListener: () => {{}}, removeEventListener: () => {{}},
    }},
    isNearBottom: () => true, scrollToBottom: () => {{}},
    currentSessionId: 's1', dlog: () => {{}},
    t: (k) => (k in TEMPLATES ? TEMPLATES[k] : k),
    localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
    sessionStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
}});
vm.runInContext(fs.readFileSync({json.dumps(str(EVENTS_JS))}, 'utf8'), ctx,
                {{ filename: 'events.js' }});
vm.runInContext({json.dumps(payload)}, ctx);
process.stdout.write(JSON.stringify(created.map((e) => e.textContent)));
"""
    proc = subprocess.run([node, "--input-type=module", "-e", harness], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node harness failed: {proc.stderr}"
    return json.loads(proc.stdout)


def _balanced_block(lines: list[str], start: int) -> list[str]:
    """Lines of the complete `if ... fi` statement beginning at `start`."""
    depth = 0
    for offset in range(start, len(lines)):
        stripped = lines[offset].strip()
        if re.match(r"^if\b.*\bthen$", stripped):
            depth += 1
        elif stripped == "fi":
            depth -= 1
            if depth == 0:
                return lines[start : offset + 1]
    raise AssertionError(f"unterminated if-statement at line {start + 1}")


def _with_crawler_reads(script: Path) -> list[str]:
    """Complete statements in the script that READ $WITH_CRAWLER."""
    lines = script.read_text(encoding="utf-8").splitlines()
    reads, i = [], 0
    while i < len(lines):
        line = lines[i]
        if "$WITH_CRAWLER" not in line:
            i += 1
            continue
        if re.match(r"^\s*if\b", line):
            block = _balanced_block(lines, i)
            reads.append("\n".join(block))
            i += lines.index(block[-1], i) + 1
            continue
        reads.append(line)
        i += 1
    return reads


def _prologue(script: Path) -> str:
    """The script's own top-level flag-initialisation / argument-parsing block."""
    lines = script.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "VOICE_PIPER=false")
        end = next(i for i, line in enumerate(lines) if 'VOICE_BACKEND="kokoro"' in line)
    except StopIteration:
        pytest.fail(f"could not locate the argument-parsing block in {script.name}")
    return "\n".join(lines[start : end + 1])


# ── compose ────────────────────────────────────────────────────────────


def test_crawler_block_is_a_service_not_a_volume():
    """Structural: the crawler block must live under `services:`, not `volumes:`.

    A YAML parse is used so this check needs no docker daemon.
    """
    doc = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    assert "crawler" in doc["services"], "crawler is not a service"
    assert "crawler" not in doc.get("volumes", {}), "crawler was parsed as a volume"
    crawler = doc["services"]["crawler"]
    assert "with-crawler" in crawler["profiles"]
    assert crawler["networks"] == ["flai_network"]
    assert not crawler.get("ports"), "crawler must not publish host ports"


def test_compose_config_renders_with_crawler():
    """The real `docker compose config` must accept the file for every profile.

    This is the check the previous round lacked: the crawler block under
    `volumes:` made *every* profile undeployable.
    """
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker CLI not available")
    if not (ROOT / ".env").exists():
        pytest.skip(".env not present; compose cannot resolve variables")

    proc = subprocess.run(
        [docker, "compose", "-f", str(COMPOSE_FILE), "config"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"docker compose config failed: {proc.stderr}"

    rendered = yaml.safe_load(proc.stdout)
    assert "crawler" not in rendered["services"], "crawler must stay behind the with-crawler profile"

    proc = subprocess.run(
        [docker, "compose", "-f", str(COMPOSE_FILE), "--profile", "with-crawler", "config"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"docker compose --profile with-crawler config failed: {proc.stderr}"

    rendered = yaml.safe_load(proc.stdout)
    crawler = rendered["services"]["crawler"]
    assert "crawler" not in rendered.get("volumes", {})
    assert not crawler.get("ports"), "crawler must not publish host ports"
    assert int(crawler["mem_limit"]) == 2 * 1024**3
    assert "flai_network" in crawler["networks"]

    web_env = rendered["services"]["web"]["environment"]
    crawler_url = web_env.get("CRAWLER_URL") if isinstance(web_env, dict) else None
    assert crawler_url == "http://flai-crawler:11235", f"web service CRAWLER_URL is {crawler_url!r}"


# ── stage labels ───────────────────────────────────────────────────────


def test_events_js_maps_cover_every_crawl_stage():
    src = EVENTS_JS.read_text(encoding="utf-8")
    for stage, key in CRAWL_STAGES.items():
        assert f"{stage}: '{key}'" in src, f"{stage} missing from STAGE_LABEL_KEYS"
    counters = re.search(r"STAGE_COUNTER_KEYS = \{(.*?)\};", src, re.S)
    assert counters, "STAGE_COUNTER_KEYS not found"
    assert "crawl_page: 'stage_crawl_page'" in counters.group(1)


def test_page_counter_reads_the_field_the_backend_sends():
    """app/queue.py emits {"stage": "crawl_page", "pages": N} — the counter chain
    must read `data.pages`, otherwise the label renders a literal `%s`."""
    queue_src = (ROOT / "app" / "queue.py").read_text(encoding="utf-8")
    payload = re.search(r'"stage":\s*"crawl_page",\s*"pages":\s*(\w+)\s*\}', queue_src)
    assert payload, "queue.py no longer publishes crawl_page with a pages field"

    src = EVENTS_JS.read_text(encoding="utf-8")
    call = re.search(r"getStageLabel\(data\.stage,\s*([^)]*)\)\)", src)
    assert call, "onTaskProgress no longer passes a counter to getStageLabel"
    chain = call.group(1)
    assert f"data.{payload.group(1)}" in chain, (
        f"onTaskProgress does not read data.{payload.group(1)} (the field queue.py sends); chain={chain!r}"
    )


@pytest.mark.parametrize("pages", [1, 7, 50])
def test_crawl_page_label_renders_the_number_not_a_literal_placeholder(pages):
    """End-to-end through the real compiled catalogs: the label the user sees
    must contain the page number and no literal `%s`."""
    sources = _chat_html_sources()
    key = CRAWL_STAGES["crawl_page"]
    for lang in ("ru", "en"):
        translated = _catalog(lang).gettext(sources[key])
        assert translated, f"{lang}: no translation for {sources[key]!r}"
        templates = dict.fromkeys(["stage_elapsed_s", "stop_generating"])
        templates.update({v: _catalog(lang).gettext(sources[v]) for v in CRAWL_STAGES.values()})
        rendered = _node_stage_labels(
            templates,
            f"onTaskProgress({{session_id:'s1', task_id:'t1', stage:'crawl_page', pages:{pages}}});",
        )
        assert rendered, f"{lang}: events.js rendered no progress element"
        label = rendered[-1]
        assert str(pages) in label, f"{lang}: {label!r} does not contain the page number {pages}"
        assert "%s" not in label, f"{lang}: {label!r} still contains a literal %s"
        assert label == translated.replace("%s", str(pages)), f"{lang}: unexpected label {label!r}"


def test_other_counter_stages_still_render():
    """Regression guard: adding data.pages must not break the pre-existing chain."""
    templates = {
        "stage_docs_found": "Found %s fragments, preparing the answer...",
        "stage_elapsed_s": "s",
        "stop_generating": "stop",
    }
    rendered = _node_stage_labels(
        templates,
        "onTaskProgress({session_id:'s1', task_id:'t2', stage:'searching_documents', chunks:12});",
    )
    assert rendered and rendered[-1] == "Found 12 fragments, preparing the answer...", rendered


# ── catalogs ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_catalog_msgids_match_the_chat_html_lookup_strings(lang):
    """Gettext looks up the SOURCE string, so the msgid must equal it exactly."""
    sources = _chat_html_sources()
    po = (ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po").read_text(encoding="utf-8")
    for key, source in sources.items():
        assert f'msgid "{source}"' in po, f"{lang}: msgid {source!r} ({key}) missing from messages.po"
        assert f'msgid "{key}"' not in po, f"{lang}: stale pseudo-msgid {key!r} still in messages.po"
        resolved = _catalog(lang).gettext(source)
        assert resolved != source or lang == "en", f"{lang}: {source!r} resolves to itself — dead translation"


def test_page_label_keeps_its_placeholder():
    """The %s must survive translation, otherwise the number is dropped."""
    source = _chat_html_sources()["stage_crawl_page"]
    assert "%s" in source
    for lang in ("ru", "en"):
        assert "%s" in _catalog(lang).gettext(source), f"{lang}: placeholder lost in translation"


# ── deploy scripts ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name", DEPLOY_SCRIPTS)
def test_with_crawler_is_initialised_before_every_read(name, tmp_path):
    """Execute the script's own prologue and read statements under `set -u`.

    The pre-fix scripts had no `WITH_CRAWLER=false` init, so under
    `set -euo pipefail` the very first read aborted every invocation.
    Runs in a throwaway CWD: `enable_env_features` rewrites `.env` with sed.
    """
    script = ROOT / name
    lines = script.read_text(encoding="utf-8").splitlines()
    inits = [i for i, line in enumerate(lines) if re.match(r"^WITH_CRAWLER=false\s*$", line)]
    assert inits, f"{name}: no top-level WITH_CRAWLER=false initialisation"

    reads = _with_crawler_reads(script)
    assert reads, f"{name}: no read of WITH_CRAWLER found — is the feature still wired?"

    (tmp_path / ".env").write_text("CRAWL_ENABLED=false\nCRAWLER_URL=http://flai-crawler:11235\n", encoding="utf-8")
    stubs = 'info() { :; }\nwarn() { :; }\nerror() { echo "$1" >&2; exit 1; }\nusage() { :; }\n'
    program = "set -euo pipefail\nset --\n" + stubs + _prologue(script) + "\n" + "\n".join(reads) + "\n:\n"
    proc = subprocess.run(["bash", "-c", program], capture_output=True, text=True, check=False, cwd=tmp_path)
    assert proc.returncode == 0, f"{name} aborts on a WITH_CRAWLER read under set -euo pipefail: {proc.stderr}"


@pytest.mark.parametrize("name", DEPLOY_SCRIPTS)
def test_with_crawler_flag_toggles_the_env_key(name):
    """`--with-crawler` must enable CRAWL_ENABLED in .env, parity with --with-search."""
    src = (ROOT / name).read_text(encoding="utf-8")
    assert re.search(r"^\s*--with-crawler\)\s+WITH_CRAWLER=true\s*;;\s*$", src, re.M), f"{name}: flag not parsed"
    lines = src.splitlines()
    start = next(i for i, line in enumerate(lines) if 'if [[ "$WITH_CRAWLER" == "true" ]]; then' in line)
    block = "\n".join(_balanced_block(lines, start))
    assert "CRAWL_ENABLED" in block, f"{name}: --with-crawler does not touch CRAWL_ENABLED"
    assert "CRAWL_ENABLED=true" in block
    assert "CRAWL_ENABLED=false" in block
    # parity: the sibling feature uses the same info + sed shape
    assert re.search(r'info "[^"]*Crawl4AI', block), f"{name}: no info line for the crawler feature"
