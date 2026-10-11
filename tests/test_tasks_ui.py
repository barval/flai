# tests/test_tasks_ui.py
"""Tasks (Задачи) frontend checks: tab order, panel, conversation view,
stop/answer/delete flows and the SSE dispatch.

Structural tests over the shipped JS/HTML/CSS (no live browser), mirroring
test_document_folders_ui.py and the catalog checks in test_crawl_ui.py. Server
behaviour (delete endpoint, ownership, step rendering) lives in
test_tasks_api.py; the displayMessage wrapper guard in test_js_signatures.py.
"""

import gettext
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHAT_HTML = (ROOT / "app/templates/chat.html").read_text(encoding="utf-8")
CHAT_TASKS = (ROOT / "app/static/js/chat-tasks.js").read_text(encoding="utf-8")
CHAT_DOCS = (ROOT / "app/static/js/chat-documents.js").read_text(encoding="utf-8")
CHAT_INIT = (ROOT / "app/static/js/chat-init.js").read_text(encoding="utf-8")
CHAT_SESSIONS = (ROOT / "app/static/js/chat-sessions.js").read_text(encoding="utf-8")
EVENTS_JS = (ROOT / "app/static/js/events.js").read_text(encoding="utf-8")
CHAT_EXPORT = (ROOT / "app/static/js/chat-export.js").read_text(encoding="utf-8")
CHAT_CSS = (ROOT / "app/static/css/chat.css").read_text(encoding="utf-8")
DARK_CSS = (ROOT / "app/static/css/dark-theme.css").read_text(encoding="utf-8")
ROUTES_TASKS = (ROOT / "app/routes/tasks.py").read_text(encoding="utf-8")

# msgid → JS TRANSLATIONS key that looks it up in chat.html
UI_MSGIDS = {
    "Tasks": "tasks",
    "New task": "new_task",
    "Task": "task",
    "Task request": "task_request_label",
    "Create task": "create_task",
    "Stop task": "stop_task",
    "Delete task": "delete_task",
    "Delete task {title} from {date}?": "delete_task_confirm",
    "Running (step {n} of {m})": "task_status_running",
    "Running": "task_running",
    "Frozen": "task_status_frozen",
    "Completed": "task_status_completed",
    "Cancelled": "task_status_cancelled",
    "Failed": "task_status_failed",
    "Expired": "task_status_expired",
    "Answer the clarifying question…": "task_answer_placeholder",
    "No tasks yet": "task_no_tasks",
    "Export is unavailable while viewing a task": "task_export_unavailable",
}

TASK_JS_FUNCTIONS = (
    "loadTasksFromServer",
    "renderTasksList",
    "openTaskView",
    "closeTaskView",
    "loadTaskConversation",
    "sendTaskAnswer",
    "stopCurrentTask",
    "deleteTask",
    "onTaskUpdate",
    "initTasksView",
    "taskStatusLine",
)


class TestTasksTabMarkup:
    def test_tasks_tab_sits_between_documents_and_sessions(self):
        idx_docs = CHAT_HTML.index('data-view="documents"')
        idx_tasks = CHAT_HTML.index('data-view="tasks"')
        idx_sessions = CHAT_HTML.index('data-view="sessions"')
        assert idx_docs < idx_tasks < idx_sessions

    def test_tab_has_count_and_new_button(self):
        assert 'class="header-tab tasks-tab' in CHAT_HTML
        assert 'id="tasks-count"' in CHAT_HTML
        assert 'id="new-task-button"' in CHAT_HTML
        assert "➕" in CHAT_HTML

    def test_tasks_list_panel(self):
        assert 'class="tasks-list hidden" id="tasks-list"' in CHAT_HTML

    def test_script_included_before_events_js(self):
        assert "js/chat-tasks.js" in CHAT_HTML
        idx_tasks = CHAT_HTML.index("js/chat-tasks.js")
        idx_events = CHAT_HTML.index("js/events.js")
        assert idx_tasks < idx_events

    def test_header_has_task_title_span(self):
        assert 'id="task-title-text"' in CHAT_HTML
        assert 'id="chat-title-text"' in CHAT_HTML

    def test_stop_task_button_present(self):
        assert 'id="stop-task-button"' in CHAT_HTML
        assert "■" in CHAT_HTML

    def test_new_task_modal(self):
        assert 'id="new-task-modal"' in CHAT_HTML
        assert 'id="new-task-request"' in CHAT_HTML
        assert 'id="new-task-error"' in CHAT_HTML
        assert 'id="new-task-submit"' in CHAT_HTML


class TestTasksTranslations:
    def test_chat_html_exports_all_ui_keys(self):
        for js_key in UI_MSGIDS.values():
            assert f"'{js_key}':" in CHAT_HTML, f"'{js_key}' missing from chat.html TRANSLATIONS"

    def test_msgids_resolve_in_both_compiled_catalogs(self):
        for lang in ("en", "ru"):
            mo = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.mo"
            po = (ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po").read_text(encoding="utf-8")
            for msgid, js_key in UI_MSGIDS.items():
                m = re.search(r"'" + re.escape(js_key) + r"':\s*\{\{\s*_\(\"([^\"]*)\"\)\|tojson\s*\}\}", CHAT_HTML)
                assert m, f"{js_key} missing from chat.html TRANSLATIONS"
                source = m.group(1)
                assert source == msgid, f"{js_key} looks up {source!r}, expected {msgid!r}"
                assert f'msgid "{msgid}"' in po, f"{lang}: msgid {msgid!r} missing from messages.po"
                # The compiled catalog must resolve it too (stale .mo fails).
                with mo.open("rb") as fh:
                    resolved = gettext.GNUTranslations(fh).gettext(msgid)
                if lang == "en":
                    assert resolved == msgid
                else:
                    assert resolved != msgid and resolved.strip(), f"ru: {msgid!r} resolves to itself"


class TestTasksFrontendLogic:
    def test_core_functions_defined(self):
        for name in TASK_JS_FUNCTIONS:
            assert f"function {name}" in CHAT_TASKS, f"{name} missing from chat-tasks.js"

    def test_api_endpoints_used(self):
        assert "fetch('/api/tasks')" in CHAT_TASKS
        for suffix in ("'/messages'", "'/answer'", "'/stop'", "'/delete'"):
            assert suffix in CHAT_TASKS, f"{suffix} missing from chat-tasks.js"

    def test_list_item_markup(self):
        assert 'class="task-item ' in CHAT_TASKS
        assert 'class="task-status-line"' in CHAT_TASKS
        assert "task-delete-button" in CHAT_TASKS
        assert "data-task-id=" in CHAT_TASKS

    def test_conversation_reuses_chat_message_classes(self):
        assert "user-message" in CHAT_TASKS
        assert "assistant-message bot-message" in CHAT_TASKS
        assert "message-content" in CHAT_TASKS
        assert "DOMPurify.sanitize(marked.parse(" in CHAT_TASKS

    def test_answer_is_gated_on_awaiting_status(self):
        assert "task.status !== 'awaiting_user'" in CHAT_TASKS
        assert "task_answer_placeholder" in CHAT_TASKS

    def test_stop_and_delete_follow_the_session_pattern(self):
        # The input is read-only while the task is not awaiting an answer, and
        # the send flow routes through sendTaskAnswer (chat-init.js hook).
        assert "input.readOnly = !awaiting" in CHAT_TASKS
        assert "window.sendTaskAnswer" in CHAT_INIT
        assert "window.currentTaskId" in CHAT_INIT

    def test_sse_handler_handles_deleted_and_running(self):
        assert "data.status === 'deleted'" in CHAT_TASKS
        assert "scheduleTaskReload(data.task_id)" in CHAT_TASKS

    def test_view_switching_handles_tasks(self):
        assert "view === 'tasks'" in CHAT_DOCS
        assert "loadTasksFromServer" in CHAT_DOCS
        assert "['sessions', 'documents', 'tasks']" in CHAT_DOCS

    def test_session_switch_closes_the_task_view(self):
        assert "closeTaskView" in CHAT_SESSIONS

    def test_events_js_dispatches_task_update(self):
        assert "case 'task_update':" in EVENTS_JS
        assert "onTaskUpdate(event.data)" in EVENTS_JS

    def test_css_classes_exist(self):
        for cls in (
            ".tasks-list",
            ".task-item",
            ".task-status-line",
            ".task-status-banner",
            ".tasks-empty",
            ".new-task-request",
        ):
            assert cls in CHAT_CSS, f"{cls} missing from chat.css"
        assert ".dark-theme .task-item" in DARK_CSS

    def test_messages_serializer_carries_created_at(self):
        # renderTaskMessage sets data-timestamp from it; task_steps has no
        # created_at, so a step's timestamp is its started_at.
        assert '"created_at": _iso(row.get("started_at") or row.get("finished_at"))' in ROUTES_TASKS


class TestReviewGuards:
    """Regression guards from the Task 10 review round."""

    @staticmethod
    def _function_block(source, signature):
        start = source.index(signature)
        end = source.find("\nfunction ", start + 1)
        return source[start : end if end > start else len(source)]

    def test_events_js_chat_render_paths_respect_task_view(self):
        # The chat SSE render paths must not interleave into the open task
        # conversation: every DOM-mutating handler checks isTaskViewActive().
        for name in (
            "onStreamToken",
            "onMessageNew",
            "onCameraImage",
            "onToolCall",
            "onTaskProgress",
            "onVideoStep",
            "onImageStep",
            "onImagePreview",
        ):
            block = self._function_block(EVENTS_JS, f"function {name}(")
            assert "isTaskViewActive" in block, f"{name} does not check isTaskViewActive()"

    def test_result_paths_suppress_display_message(self):
        # Result-completion paths render through displayMessage only when the
        # task view is closed.
        for name in ("handleCompletedResult", "handleErrorResult", "handleCameraResult", "handleTranscriptionResult"):
            block = self._function_block(EVENTS_JS, f"function {name}(")
            assert "isTaskViewActive" in block, f"{name} does not check isTaskViewActive()"

    def test_close_task_view_uses_lexical_session_id(self):
        # window.currentSessionId is never assigned anywhere (currentSessionId
        # is a top-level `let` in chat-constants.js, a lexical global) — the
        # reload after closing a task view must consult the lexical binding,
        # not a window property. The comment in closeTaskView documents this;
        # the CODE itself must not dereference window.currentSessionId.
        assert "window.currentSessionId" not in re.sub(r"//.*", "", CHAT_TASKS)
        assert "window.loadMessages(currentSessionId)" in CHAT_TASKS

    def test_active_task_without_steps_is_not_queued(self):
        # Plan phase (active, total_steps=0) must not display "queued".
        assert "task_running" in CHAT_TASKS
        assert "t('task_status_running')" in CHAT_TASKS

    def test_export_blocked_during_task_view(self):
        assert "window.currentTaskId" in CHAT_EXPORT
        assert "task_export_unavailable" in CHAT_EXPORT

    def test_new_task_modal_escape_closes(self):
        assert "onNewTaskModalKeydown" in CHAT_TASKS
        assert "closeNewTaskModal" in CHAT_TASKS

    def test_answer_restored_on_error(self):
        # A failed answer POST must not wipe the typed text.
        answer_block = self._function_block(CHAT_TASKS, "function sendTaskAnswer(")
        assert "input.value = text" in answer_block

    def test_task_modal_has_no_dead_attributes(self):
        assert "data-task-title" not in CHAT_TASKS
