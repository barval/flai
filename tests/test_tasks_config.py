# tests/test_tasks_config.py
from flask import current_app


def test_task_config_defaults(test_app):
    with test_app.app_context():
        assert current_app.config["TASKS_ENABLED"] is True
        assert current_app.config["TASK_QUIET_PERIOD_S"] == 15
        assert current_app.config["TASK_TOTAL_BUDGET_S"] == 86400
        assert current_app.config["TASK_STEP_MAX_ATTEMPTS"] == 3
        assert current_app.config["TASK_POLL_INTERVAL_S"] == 1
        assert current_app.config["TASK_SUBTASK_POLL_CYCLES"] == 10
