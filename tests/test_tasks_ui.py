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
    "Transcribing audio…": "task_status_transcribing",
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

    @staticmethod
    def _block(source, start_anchor, end_anchor):
        start = source.index(start_anchor)
        end = source.index(end_anchor, start) + len(end_anchor)
        return source[start:end]

    @staticmethod
    def _css_media_block(css, anchor="    .header-tabs {"):
        """The @media block containing the anchor (mobile overrides)."""
        start = css.rindex("@media", 0, css.index(anchor))
        end = css.find("\n}", css.index(anchor))
        return css[start:end]

    def test_mobile_tasks_panel_full_screen(self):
        """A task opened from the list takes the whole area between the tab
        headers and the footer (same as the chat panel): the tasks list must be
        part of BOTH mobile panel selectors."""
        idx_hide = CHAT_CSS.index(".sessions-list,\n    .documents-list,\n    .tasks-list {")
        idx_show = CHAT_CSS.index(
            ".sessions-sidebar.panel-open .sessions-list.active,"
            "\n    .sessions-sidebar.panel-open .documents-list.active,"
            "\n    .sessions-sidebar.panel-open .tasks-list.active {"
        )
        assert idx_hide < idx_show
        assert ".sessions-sidebar.panel-open ~ .chat-main {" in CHAT_CSS

    def test_mobile_tabs_match_footer_height(self):
        """The tab-headers block is exactly as tall as the footer bar (30px),
        and the tabs own the height (no vertical padding inflation). The
        horizontal gaps around the tab titles are halved (0.15rem)."""
        block = self._css_media_block(CHAT_CSS)
        assert "height: 30px" in block
        assert "padding: 0 0.15rem" in block

    def test_mobile_header_bar_hugs_save_button(self):
        """The «Чат/Задача 📊 …» bar hugs the save button's bottom edge with a
        top allowance (5px): smaller tops clipped the glyph tops."""
        block = self._css_media_block(CHAT_CSS, anchor="    .chat-header {")
        assert "padding: 7px 0 0;" in block
        assert "align-items: stretch;" in block
        idx = CHAT_CSS.index(".save-chat-button,\n    .cancel-stream-header {")
        assert "align-self: flex-end" in CHAT_CSS[idx : idx + 120]

    def test_mobile_controls_row_hugs_send_button(self):
        """The «Глубокий анализ / 🎤 / 📎 / Отправить» row stretches every
        control edge-to-edge with the Send button and has NO vertical gaps:
        the chat/task output window starts right above the row, the message
        input right below it. Selectors carry .chat-container because the
        base .rlm-controls rule sits AFTER this media block and would win a
        same-specificity tie."""
        block = self._css_media_block(CHAT_CSS, anchor="    .chat-container .rlm-controls {")
        assert "align-items: stretch;" in block
        assert "margin-top: 0;" in block
        assert "margin-bottom: 0;" in block
        assert "    .chat-container .rlm-controls label {\n        align-self: center;" in block
        msgs = self._block(CHAT_CSS, "    .chat-messages {", "    .message-input {")
        assert "margin-bottom: 0;" in msgs
        input_block = self._block(CHAT_CSS, '    .message-input input[type="text"] {', "}")
        assert "height: 2rem;" in input_block, "the text input matches the Send button's height"

    def test_tts_head_spacing_halved(self):
        """The 🗣️ head (and its 📋 neighbour) halve their spacing in message
        headers: 4px left margin, 3px+1px padding instead of 8px/6px."""
        for cls in (".tts-button {", ".copy-message-button {"):
            block = self._block(CHAT_CSS, cls, "}")
            assert "margin-left: 4px" in block, cls
            assert "padding: 2px 3px" in block, cls
        media = self._css_media_block(CHAT_CSS)
        assert "gap: 0.25rem" in self._block(media, ".message-header {", "}")

    def test_header_row_matches_tab_font_and_half_divider_gaps(self):
        """The «Чат/Задача 📊 … | Стиль: …» row uses the tab headers' font size
        (desktop 1rem / mobile 0.85rem) and the divider gaps are half the old
        two-nbsp padding (user request)."""
        assert "font-size: 1rem" in self._block(CHAT_CSS, ".chat-header h2 {", "}")
        media = self._css_media_block(CHAT_CSS)
        h2 = self._block(media, ".chat-header h2 {", "}")
        assert "font-size: 0.85rem" in h2, "the row matches the mobile tab font"
        line = next(ln for ln in CHAT_HTML.splitlines() if 'id="status-counter"' in ln)
        assert '&nbsp;<span class="header-divider">|</span>&nbsp;' in line
        assert '&nbsp;&nbsp;<span class="header-divider">' not in line

    def test_stop_button_replaces_the_save_button(self):
        """The ■ replaces the 💾 in the header row while the task is processing
        (the chat's own stream swap pattern) and the 💾 returns when the task
        stops/finishes — both bars keep their exact height."""
        block = self._function_block(CHAT_TASKS, "function setTaskMode(")
        assert "getElementById('save-chat-button')" in block
        assert "saveBtn.style.display = active && !stopped ? 'none' : ''" in block

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

    def test_paperclip_documents_ride_with_task_send(self):
        """Documents attached with the paperclip (the chat chips) must reach the
        task as FULL user documents: the helper collects the multi-queue AND the
        legacy single slot (a lone attached document sat there and was dropped
        silently), and the multipart builder ships them as 'files' parts that
        the server saves through the Documents-panel intake chain."""
        helper = self._function_block(CHAT_TASKS, "function taskDocumentFiles(")
        assert "attachedFiles" in helper and "isDocumentFile" in helper
        assert "attachedFile" in helper, "the legacy single slot holds the first non-image file"
        assert "unshift" in helper, "the legacy-slot file leads the queue order"
        builder = self._arrow_block(CHAT_TASKS, "const formDataFor = ")
        assert "fd.append('files', doc)" in builder
        send = self._function_block(CHAT_TASKS, "function sendTaskInput(")
        assert "taskDocumentFiles()" in send
        # A documents-only submit must not silently do nothing (the server
        # answers with the localized "Task request is empty").
        assert "documents.length === 0" in send
        assert "def _save_task_documents(" in ROUTES_TASKS
        assert ROUTES_TASKS.count('request.files.getlist("files")') == 2, "both /request and /answer accept files[]"
        # The files ride as documents scoping the task, merged with the panel
        # selection (a submit that attached files must never mean "all docs").
        assert "update_task(task_id, doc_ids=new_doc_ids + " in ROUTES_TASKS
        assert ROUTES_TASKS.count("new_doc_ids + ") == 2

    def test_audio_attachments_ride_with_task_send(self):
        """Voice and audio reach a task like they do in chat (user request:
        «вставка любых типов вложений в задачи должна быть как и в чате»): the
        microphone blob and every audio chip are collected and shipped as
        'audio' parts the server transcribes into the task's context."""
        helper = self._function_block(CHAT_TASKS, "function taskAudioFiles(")
        assert "isAudioTypeFile" in helper
        assert "attachedVoiceBlob" in helper, "a recording paired with an image must not be dropped"
        assert "attachedFile" in helper, "a lone recording parks in the legacy single slot"
        builder = self._arrow_block(CHAT_TASKS, "const formDataFor = ")
        assert "fd.append('audio', a)" in builder
        send = self._function_block(CHAT_TASKS, "function sendTaskInput(")
        assert "taskAudioFiles()" in send
        assert "audio.length === 0" in send, "an audio-only submit must not silently do nothing"
        # The recorder flags the chat as transcribing; the task reports its own
        # progress through task events, so that flag must not linger.
        assert "clearTaskVoiceState()" in send
        voice_reset = self._function_block(CHAT_TASKS, "function clearTaskVoiceState(")
        assert "attachedVoiceBlob = null" in voice_reset and "isVoiceRecorded = false" in voice_reset
        assert "setLocalTranscribing" in voice_reset
        assert ROUTES_TASKS.count('request.files.getlist("audio")') == 2, "both /request and /answer accept audio"
        assert "def _save_task_audio(" in ROUTES_TASKS
        # One voice message plus audio files, at most MAX_CHAT_AUDIO per submit
        # (chat parity): the excess is refused with a localized message.
        assert 'current_app.config.get("MAX_CHAT_AUDIO", 4)' in ROUTES_TASKS
        assert '_("max_audio_reached")' in ROUTES_TASKS

    def test_audio_transcript_renders_and_transcribing_status(self):
        """The attachment line (📎, plan line font), the transcript in the
        regular reading font and a «transcribing» status while the task waits
        for Whisper."""
        assert "'attached_audio'" in CHAT_TASKS, "the attachment line is a service line"
        assert "audio_transcripts" in CHAT_TASKS and "task-msg-transcript" in CHAT_TASKS
        assert ".task-msg-transcript" in CHAT_CSS, "a long transcript needs the regular font"
        assert "task_status_transcribing" in CHAT_TASKS
        assert "abort_reason === 'audio_transcript'" in CHAT_TASKS
        assert "def _transcript_block(" in ROUTES_TASKS

    def test_deleting_the_open_task_stays_in_tasks_view(self):
        """Deleting the open task opens another task (or the empty state) — the
        main window must never fall back to the chat session (user request:
        «после её удаления в окне задач должна открыться другая задача»)."""
        helper = self._function_block(CHAT_TASKS, "function replaceClosedTask(")
        assert "closeTaskView(true)" in helper
        assert "Object.values(tasksData)" in helper
        assert "openTaskView(" in helper and "ensureFirstTask()" in helper
        # Every "the open task is gone" path routes through it: the delete
        # button, the SSE `deleted` event and a list reload after a deletion in
        # another tab.
        for name in ("deleteTask", "onTaskUpdate", "loadTasksFromServer"):
            block = self._function_block(CHAT_TASKS, f"function {name}(")
            assert "replaceClosedTask()" in block, f"{name} must not return to the chat window"
            assert "closeTaskView(false)" not in block, f"{name} still falls back to the chat"

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
        re-entry (tab switch and page reload), and when there are no tasks the
        first draft task is created automatically (chat parity). The empty state
        remains only as the failure fallback."""
        assert "restoreTasksView" in CHAT_DOCS and "leaveTasksView" in CHAT_DOCS
        assert "enterTasksEmptyState" in CHAT_DOCS  # fallback only
        assert "task-empty-state" in CHAT_TASKS
        # Remembered across tab switches and reloads (per user, like the view).
        assert "last_task_" in CHAT_TASKS
        assert "rememberTaskId" in CHAT_TASKS and "readRememberedTaskId" in CHAT_TASKS
        assert "forgetTaskId" in CHAT_TASKS
        assert "rememberTaskId(taskId)" in self._function_block(CHAT_TASKS, "function openTaskView(")
        restore_block = self._function_block(CHAT_TASKS, "function restoreTasksView(")
        assert "loadTasksFromServer" in restore_block and "openTaskView(remembered)" in restore_block
        assert "ensureFirstTask()" in restore_block
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
        # Tokens are gated on the usage, not on the duration; `usage` itself is
        # already gated on !isUser, so a user message never yields tokens.
        assert "if (hasTokens)" in block
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

    def test_request_attachments_are_a_service_line(self):
        """Images and documents attached to a task render as service lines (the
        plan line's font) with the 📎 marker and one file name per line — the
        server ships the ready localized line in its own field, never appended
        to the request body (user request)."""
        block = self._function_block(CHAT_TASKS, "function renderTaskMessage(")
        assert "['attached_images', 'attached_documents', 'attached_audio']" in block
        assert "'📎 ' + m[key]" in block
        assert "task-msg-service" in block
        # The server builds the lines (the label on its own line, then one file
        # name per line — the task view keeps the newlines) and never rides the
        # attachment names inside the request text.
        assert '"attached_images": attached_images' in ROUTES_TASKS
        assert '"attached_documents": attached_documents' in ROUTES_TASKS
        assert '"attached_audio": attached_audio' in ROUTES_TASKS
        # A newline starts the names block, so the label never shares its line
        # with the first file name (user request).
        assert 'names="\\n" + "\\n".join(image_names)' in ROUTES_TASKS
        assert 'names="\\n" + "\\n".join(doc_names)' in ROUTES_TASKS
        assert 'names="\\n" + "\\n".join(req_audio_names)' in ROUTES_TASKS
        assert 'names="\\n" + "\\n".join(names)' in ROUTES_TASKS
        assert 'names=", ".join(image_names)' not in ROUTES_TASKS
        # Document names resolve through the user's document list (doc_ids are
        # ids only) and a deleted document is skipped.
        assert '"Attached documents: {names}"' in ROUTES_TASKS
        assert '"Attached images: {names}"' in ROUTES_TASKS
        assert "def _document_names(" in ROUTES_TASKS
        assert "get_user_documents(" in ROUTES_TASKS

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

    def test_all_attached_images_rendered(self):
        """Every image of the request message renders (bug: 4 attached images
        showed 1 — the server exposes extra.media_all and the JS loops it)."""
        block = self._function_block(CHAT_TASKS, "function renderTaskMessage(")
        assert "media_all" in block, "the render must loop over extra.media_all"
        assert "forEach" in block, "media blocks render in a loop"

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


class TestTasksFirstTaskAutoCreate:
    """When the Tasks list is empty the first draft task is created
    automatically (chat parity: no empty session list), so the composer is
    never a half-dead «empty state».
    """

    @staticmethod
    def _function_block(source, signature):
        start = source.index(signature)
        end = source.find("\nfunction ", start + 1)
        return source[start : end if end > start else len(source)]

    def test_first_task_is_created_when_the_list_is_empty(self):
        assert "function ensureFirstTask(" in CHAT_TASKS
        helper = self._function_block(CHAT_TASKS, "function ensureFirstTask(")
        # Creates a draft task and opens it.
        assert "/api/tasks" in helper
        assert "openTaskView" in helper
        # Creation failure is the ONLY way back to the empty state.
        assert "enterTasksEmptyState" in helper
        # Idempotent: an existing task is opened, never duplicated.
        assert "Object.keys(tasksData)" in helper

    def test_restore_creates_the_first_task_instead_of_the_empty_state(self):
        restore = self._function_block(CHAT_TASKS, "function restoreTasksView(")
        assert "ensureFirstTask()" in restore
        # loadTasksFromServer returns a success/fail flag so fetch errors do
        # not auto-create (would duplicate existing tasks).
        assert "loadTasksFromServer().then(" in restore

    def test_replacing_a_closed_task_creates_a_new_one(self):
        helper = self._function_block(CHAT_TASKS, "function replaceClosedTask(")
        assert "ensureFirstTask()" in helper
        assert "closeTaskView(true)" in helper

    def test_load_tasks_reports_fetch_failure(self):
        block = self._function_block(CHAT_TASKS, "function loadTasksFromServer(")
        assert "return true" in block and "return false" in block
