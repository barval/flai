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
CHAT_UTILS = (ROOT / "app/static/js/chat-utils.js").read_text(encoding="utf-8")
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
    "Awaiting answer": "task_awaiting_answer",
    "Answer the clarifying question…": "task_answer_placeholder",
    "Draft": "task_status_draft",
    "Draft — describe the task…": "task_draft_placeholder",
    "No task selected — use «+» to create one": "task_empty_state",
    "No tasks yet": "task_no_tasks",
    "Export is unavailable while viewing a task": "task_export_unavailable",
}

TASK_JS_FUNCTIONS = (
    "loadTasksFromServer",
    "renderTasksList",
    "openTaskView",
    "closeTaskView",
    "enterTasksEmptyState",
    "leaveTasksView",
    "loadTaskConversation",
    "sendTaskInput",
    "createNewTask",
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

    def test_stop_button_visibility_uses_inline_display(self):
        """The button is shown/hidden with an inline display: the `hidden` class
        is `display: none !important` (base.css) and silently beat setTaskMode's
        inline style — the button existed but never appeared in task mode.
        """
        line = next(ln for ln in CHAT_HTML.splitlines() if 'id="stop-task-button"' in ln)
        assert "hidden" not in line, "the !important class would override the inline display"
        assert "cancel-stream-header" in line, "chat mode still hides it (display: none by default)"
        assert "stopBtn.style.display" in CHAT_TASKS

    def test_new_task_modal(self):
        """Removed (v12.6 review): «+» creates a draft task like a new chat session."""
        assert 'id="new-task-modal"' not in CHAT_HTML


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

    def test_tool_badges_localized_in_both_catalogs(self):
        """Server-rendered tool labels (TOOL_BADGES msgids) resolve in en and ru."""
        from app.tasks.tools import TOOL_BADGES

        for lang in ("en", "ru"):
            mo = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.mo"
            with mo.open("rb") as fh:
                catalog = gettext.GNUTranslations(fh)
            for _emoji, msgid in TOOL_BADGES.values():
                resolved = catalog.gettext(msgid)
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
        # The input is read-only while the task is neither awaiting nor a
        # draft, and the send flow routes through sendTaskInput (chat-init.js
        # hook).
        assert "input.readOnly = !awaiting && !draft" in CHAT_TASKS
        assert "window.sendTaskInput" in CHAT_INIT
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

    def test_events_js_dispatches_task_stage(self):
        assert "case 'task_stage':" in EVENTS_JS
        assert "onTaskStage(event.data)" in EVENTS_JS

    def test_stage_label_registered(self):
        """The plan-phase stage resolves through the chat's stage-label map."""
        assert "making_plan: 'stage_making_plan'" in EVENTS_JS
        assert "'stage_making_plan':" in CHAT_HTML
        assert "startStageTimer" in CHAT_TASKS

    def test_plan_stage_msgids_resolve_in_both_compiled_catalogs(self):
        for lang in ("en", "ru"):
            mo = ROOT / "translations" / lang / "LC_MESSAGES" / "messages.mo"
            with mo.open("rb") as fh:
                resolved = gettext.GNUTranslations(fh).gettext("Making the plan...")
            if lang == "en":
                assert resolved == "Making the plan..."
            else:
                assert resolved != "Making the plan..." and "Составляю план" in resolved

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

    @staticmethod
    def _arrow_block(source, signature):
        """Block of a const arrow function (``const name = (...) => {``)."""
        start = source.index(signature)
        end = source.find("\n    };", start + 1)
        return source[start : end + 6 if end > start else len(source)]

    def test_documents_ride_with_task_send(self):
        """The shared Documents-panel selection is snapshotted into the task
        request/answer (the same #rlm-docs multi-select the chat reads)."""
        helper = self._function_block(CHAT_TASKS, "function taskDocIds(")
        assert "'rlm-docs'" in helper
        assert "selectedOptions" in helper
        # The multipart builder is used by BOTH submits (/request and /answer):
        # it must read the doc selection and the 🔬 toggle together.
        builder = self._arrow_block(CHAT_TASKS, "const formDataFor = ")
        assert builder.count("taskDocIds()") == 1 and "doc_ids" in builder
        assert "taskDeepAnalysis()" in builder
        send = self._function_block(CHAT_TASKS, "function sendTaskInput(")
        assert send.count("formDataFor(") == 2, "both /request and /answer use the multipart builder"
        assert "doc_ids" in send and "deep_analysis" in send

    def test_images_ride_with_task_send(self):
        """Attached images (v12.6 vision) ride both submits as multipart parts."""
        helper = self._function_block(CHAT_TASKS, "function taskImageFiles(")
        assert "attachedFiles" in helper and "isImageFile" in helper
        builder = self._arrow_block(CHAT_TASKS, "const formDataFor = ")
        assert builder.count("images") >= 2, "builder appends every queued image as 'images'"
        send = self._function_block(CHAT_TASKS, "function sendTaskInput(")
        assert "taskImageFiles()" in send

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

    def test_list_badges_follow_the_session_pattern(self):
        """A queued/active/awaiting row carries a blinking session-style badge
        before its title: hourglass + queue position, lightning, question mark.
        The small status line below the title stays (terminal states only)."""
        block = self._function_block(CHAT_TASKS, "function taskStatusIcon(")
        assert "session-status-icon" in block
        assert "'active'" in block and "processing blink" in block and "⚡" in block
        assert "'awaiting_user'" in block and "awaiting blink" in block and "❓" in block
        assert "'queued'" in block and "task.queue_position" in block and "⏳" in block
        # The badge precedes the title in the list row.
        assert "taskStatusIcon(task)" in self._function_block(CHAT_TASKS, "function renderTasksList(")
        # Queue positions are global → a status event re-reads the list.
        assert "scheduleTasksListReload" in self._function_block(CHAT_TASKS, "function onTaskUpdate(")
        assert ".session-status-icon.awaiting" in CHAT_CSS

    def test_export_blocked_during_task_view(self):
        assert "window.currentTaskId" in CHAT_EXPORT
        assert "task_export_unavailable" in CHAT_EXPORT

    def test_new_task_modal_removed(self):
        """The new-task dialog is gone: «+» creates a draft directly."""
        assert 'id="new-task-modal"' not in CHAT_HTML
        assert "openNewTaskModal" not in CHAT_TASKS
        assert "closeNewTaskModal" not in CHAT_TASKS
        assert "createNewTask" in CHAT_TASKS

    def test_answer_restored_on_error(self):
        # A failed answer/draft POST must not wipe the typed text.
        answer_block = self._function_block(CHAT_TASKS, "function sendTaskInput(")
        assert "input.value = text" in answer_block

    def test_awaiting_banner_is_the_chat_style_live_status(self):
        """Every live status renders the pulsing banner (chat stage look);
        awaiting_user additionally ticks the elapsed-seconds counter."""
        assert "task-await-banner" in CHAT_TASKS
        assert "progressPulse" in CHAT_CSS
        assert "startAwaitTimer" in CHAT_TASKS and "stopAwaitTimer" in CHAT_TASKS
        assert "task_awaiting_answer" in CHAT_TASKS
        # queued/active/frozen/awaiting_user are all live (pulsing).
        assert "'queued', 'active', 'frozen', 'awaiting_user'" in CHAT_TASKS

    def test_conversation_payload_deduplicated(self):
        """An identical payload does not rebuild the DOM (anti-flicker)."""
        assert "lastTaskPayloadJson" in CHAT_TASKS

    def test_empty_state_wired_into_view_switch(self):
        """The Tasks tab owns the main window; the last task is restored on
        re-entry (tab switch and page reload), the empty state is the fallback."""
        assert "restoreTasksView" in CHAT_DOCS and "leaveTasksView" in CHAT_DOCS
        assert "enterTasksEmptyState" in CHAT_DOCS
        assert "task-empty-state" in CHAT_TASKS
        # Remembered across tab switches and reloads (per user, like the view).
        assert "last_task_" in CHAT_TASKS
        assert "rememberTaskId" in CHAT_TASKS and "readRememberedTaskId" in CHAT_TASKS
        assert "forgetTaskId" in CHAT_TASKS
        assert "rememberTaskId(taskId)" in self._function_block(CHAT_TASKS, "function openTaskView(")
        restore_block = self._function_block(CHAT_TASKS, "function restoreTasksView(")
        assert "loadTasksFromServer" in restore_block and "openTaskView(remembered)" in restore_block
        # A remembered task deleted meanwhile is forgotten, not restored forever.
        assert "forgetTaskId" in self._function_block(CHAT_TASKS, "function loadTasksFromServer(")

    def test_chat_style_message_headers(self):
        """Task messages carry the chat header shape: model, ⏱️, 🔢, 🚀,
        TTS + copy buttons (user request: same format as chat messages)."""
        assert "tokenStatsHTML" in CHAT_TASKS
        assert "getModelEmoji" in CHAT_TASKS and "getResponseStyleEmoji" in CHAT_TASKS
        block = self._function_block(CHAT_TASKS, "function renderTaskMessage(")
        assert "tts-button" in block and "copy-message-button" in block
        assert "tps_suffix" in block and "seconds_suffix" in block

    def test_style_emoji_needs_a_model(self):
        """The style emoji (🤖 neutral) must NOT hang in a step header that has
        no model (a camera error before any LLM call): it renders only when the
        header also carries the model name, so an error step reads as an error,
        not as a robot answer."""
        block = self._function_block(CHAT_TASKS, "function renderTaskMessage(")
        assert "m.model_name && task && task.response_style" in block, "style emoji requires a model in the header"

    def test_header_fields_are_independent(self):
        """⏱️/🔢/🚀 stand on their own: the final summary has tokens with no
        duration column, and a tool step (calculate/search) reports its
        verifier's model — gating the token block on ``exec_seconds`` hid both."""
        block = self._function_block(CHAT_TASKS, "function renderTaskMessage(")
        assert "hasTokens" in block
        # Tokens are gated on the usage, not on the duration.
        assert "if (!isUser && hasTokens)" in block
        assert "tokenStatsHTML(promptTokens, completionTokens)" in block
        # The tool name is NOT in the header — the verifier model is — so the
        # header has no tool fallback branch.
        assert "m.tool" not in block

    def test_tool_badge_is_its_own_service_line(self):
        """A tool-produced step names its tool (localized, with an emoji) on its
        own italic line — the server sends it as ``badge`` and the client
        renders it with the same class as the step header."""
        tools_src = (ROOT / "app/tasks/tools.py").read_text(encoding="utf-8")
        assert "TOOL_BADGES" in tools_src and "🧮" in tools_src
        assert "Calculator" in tools_src and "Web search" in tools_src
        assert "_tool_badge" in ROUTES_TASKS and "TOOL_BADGES" in ROUTES_TASKS
        assert '_tool_badge(row.get("tool"))' in ROUTES_TASKS
        assert '"badge": badge' in ROUTES_TASKS
        block = self._function_block(CHAT_TASKS, "function renderTaskMessage(")
        assert "m.badge" in block and "task-msg-service" in block

    def test_step_media_rendered_chat_style(self):
        """Camera snapshots / generated media in a step's extra render as chat
        image blocks after the text body (user: «сами снимки не выводятся»)."""
        block = self._function_block(CHAT_TASKS, "function renderTaskMessage(")
        assert "extra.media" in block
        assert "image-container" in block and "attached-image" in block
        assert "openImageModal" in block
        assert "'/api/files/' + media.file_path" in block
        # Video steps render a player too.
        assert "video/" in block and "createElement('video')" in block

    def test_service_lines_styled_separately(self):
        """«План из N шагов:» / «Шаг N из M:» are their own italic line."""
        assert "splitServiceLine" in CHAT_TASKS and "task-msg-service" in CHAT_TASKS
        assert "task-msg-service" in CHAT_CSS
        assert "font-style: italic" in CHAT_CSS

    def test_service_line_split_is_language_agnostic(self):
        """The split keys on the shape (a first line ending with ':'), never on
        the localized wording: hardcoding «Шаг N из M:» once left the plan line
        («План из 1 шагов:» — ends with a colon, not a digit) unstyled."""
        block = self._function_block(CHAT_TASKS, "function splitServiceLine(")
        assert "endsWith(':')" in block
        assert "🗒️" in block and "⚙️" in block, "plan and step emojis"
        assert "Шаг" not in block and "Plan of" not in block
        assert "'plan'" in block and "'direct'" in block and "'subtask'" in block
        # The plan message the server builds is header + newline + numbered lines.
        assert '"kind": "plan"' in ROUTES_TASKS

    def test_list_items_show_token_totals_and_exec(self):
        """Task list items carry [▲requests ▼answers] token totals like session
        cards and the total ⏱️ execution time (user request)."""
        assert "sessionTokenStatsHTML" in CHAT_TASKS
        block = self._function_block(CHAT_TASKS, "function renderTasksList(")
        assert "token_totals" in block and "exec_seconds" in block
        assert "_token_totals_for" in ROUTES_TASKS and "token_totals" in ROUTES_TASKS

    def test_arrow_semantics_outgoing_first(self):
        """The arrow semantics are user-perspective EVERYWHERE (chat-utils is
        the single source shared by chat/tasks/sessions): ▲ first = the user's
        OUTGOING requests (prompt), ▼ second = the model's INCOMING answers
        (completion)."""
        token_block = self._function_block(CHAT_UTILS, "function tokenStatsHTML(")
        session_block = self._function_block(CHAT_UTILS, "function sessionTokenStatsHTML(")
        assert "'▲' + input" in token_block and "'▼' + output" in token_block, (
            "tokenStatsHTML must render ▲prompt (outgoing) first, ▼completion (incoming) second"
        )
        assert "▲' + fmt(prompt) + ' ▼' + fmt(completion)" in session_block, (
            "sessionTokenStatsHTML must render ▲prompt first, ▼completion second"
        )
        # The plan message carries the same chat-style header (model/tokens/time).
        assert '"exec_seconds": float(plan_extra.get("exec_seconds") or 0.0)' in ROUTES_TASKS
        assert '"model_name": str(plan_extra.get("model_name") or "")' in ROUTES_TASKS

    def test_user_messages_have_tts_and_copy_buttons(self):
        """🗣️ + 📋 sit in the headers of BOTH sides (user request): the task
        view renders one unconditional block, the chat adds a user branch."""
        msg_block = self._function_block(CHAT_TASKS, "function renderTaskMessage(")
        assert "if (!isUser && String(m.content" not in msg_block, "TTS must not be assistant-only"
        chat_messages = (ROOT / "app/static/js/chat-messages.js").read_text(encoding="utf-8")
        assert "if (role === 'user' && !isSystemErrorRow)" in chat_messages
        assert chat_messages.count("tts-button") >= 2, "a TTS button in both the assistant and user branches"

    def test_answer_submit_sends_answer_field(self):
        """The clarifying-question answer is submitted in the 'answer' field —
        the /answer route reads data['answer'] and answers 'Answer is empty'
        (⚠️ Ответ пуст) when it is missing. A regression here silently breaks
        every task answer."""
        # The answer branch must POST field 'answer' (not 'request').
        answer_post = CHAT_TASKS[CHAT_TASKS.index("/answer', {") :]
        assert "'answer'" in answer_post.split("})")[0], "sendTaskInput must append the answer under the 'answer' key"
        # Both submits route through the shared formDataFor(field) helper.
        assert "fd.append(field || 'request', payloadText)" in CHAT_TASKS
        # The server route reads data['answer'].
        assert 'data.get("answer")' in ROUTES_TASKS

    def test_task_modal_has_no_dead_attributes(self):
        assert "data-task-title" not in CHAT_TASKS
