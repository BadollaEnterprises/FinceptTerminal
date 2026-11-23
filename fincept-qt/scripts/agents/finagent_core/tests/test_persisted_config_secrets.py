import sqlite3

from finagent_core.agentic.runner import persistable_config
from finagent_core.agentic.scheduler import ScheduledTaskStore
from finagent_core.persistence_policy import sanitize_persisted_config
from finagent_core.task_state import TaskStateManager


def _config_with_secrets():
    return {
        "model": {
            "provider": "deepseek",
            "model_id": "deepseek-chat",
            "api_key": "model-secret",
            "max_tokens": 2048,
        },
        "api_keys": {"DEEPSEEK_API_KEY": "container-secret"},
        "nested": [
            {"authorization": "Bearer auth-secret", "temperature": 0.2},
            {"OPENAI_API_KEY": "provider-secret", "token_budget": 4000},
            {"credentials": {"client_secret": "oauth-secret", "client_id": "safe-id"}},
        ],
        "terminal_mcp_token": "bridge-secret",
        "terminal_mcp_destructive_token": "destructive-secret",
        "max_tokens": 4096,
    }


def test_recursive_sanitizer_removes_credentials_without_mutating_input():
    source = _config_with_secrets()

    sanitized = sanitize_persisted_config(source)

    assert source["model"]["api_key"] == "model-secret"
    assert sanitized["model"] == {
        "provider": "deepseek",
        "model_id": "deepseek-chat",
        "max_tokens": 2048,
    }
    assert "api_keys" not in sanitized
    assert sanitized["nested"] == [
        {"temperature": 0.2},
        {"token_budget": 4000},
        {"credentials": {"client_id": "safe-id"}},
    ]
    assert sanitized["max_tokens"] == 4096


def test_resumable_task_policy_removes_runtime_wiring_and_nested_credentials():
    config = _config_with_secrets()
    config["terminal_mcp_endpoint"] = "http://127.0.0.1:9999"
    config["terminal_tools"] = [{"name": "quote"}]
    config["__fincept_runtime_budgets"] = {"input_max_tokens": 32000}

    stored = persistable_config(config)

    assert stored["model"]["provider"] == "deepseek"
    assert stored["nested"][0] == {"temperature": 0.2}
    assert "terminal_mcp_endpoint" not in stored
    assert "terminal_mcp_token" not in stored
    assert "terminal_mcp_destructive_token" not in stored
    assert "terminal_tools" not in stored
    assert "__fincept_runtime_budgets" not in stored


def test_schedule_store_never_writes_credentials_to_sqlite(tmp_path):
    db_path = tmp_path / "schedules.db"
    store = ScheduledTaskStore(db_path=str(db_path))

    sid = store.create(
        name="Daily review",
        query="Review my holdings",
        schedule_expr="daily 09:30",
        config=_config_with_secrets(),
    )

    row = store.get(sid)
    assert row is not None
    assert row["config"]["model"]["provider"] == "deepseek"
    assert row["config"]["nested"][2] == {"credentials": {"client_id": "safe-id"}}
    assert row["config"]["max_tokens"] == 4096

    with sqlite3.connect(db_path) as conn:
        raw = conn.execute(
            "SELECT config_json FROM agent_scheduled_tasks WHERE id = ?", (sid,)
        ).fetchone()[0]
    for secret in (
        "model-secret",
        "container-secret",
        "auth-secret",
        "provider-secret",
        "oauth-secret",
        "bridge-secret",
        "destructive-secret",
    ):
        assert secret not in raw


def test_task_state_store_sanitizes_direct_callers(tmp_path):
    db_path = tmp_path / "tasks.db"
    manager = TaskStateManager(db_path=str(db_path))

    task_id = manager.create_task("Review my holdings", _config_with_secrets())

    task = manager.get_task(task_id)
    assert task is not None
    assert task["config"]["model"]["provider"] == "deepseek"
    assert task["config"]["max_tokens"] == 4096
    with sqlite3.connect(db_path) as conn:
        raw = conn.execute(
            "SELECT config_json FROM agent_tasks WHERE id = ?", (task_id,)
        ).fetchone()[0]
    assert "model-secret" not in raw
    assert "container-secret" not in raw


def test_store_initialization_scrubs_credentials_from_legacy_rows(tmp_path):
    db_path = tmp_path / "legacy.db"
    schedules = ScheduledTaskStore(db_path=str(db_path))
    sid = schedules.create("Review", "Review holdings", "hourly", {})
    tasks = TaskStateManager(db_path=str(db_path))
    task_id = tasks.create_task("Review holdings", {})
    unsafe = '{"model":{"provider":"deepseek","api_key":"legacy-secret"},"max_tokens":99}'
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE agent_scheduled_tasks SET config_json = ? WHERE id = ?",
            (unsafe, sid),
        )
        conn.execute(
            "UPDATE agent_tasks SET config_json = ? WHERE id = ?",
            (unsafe, task_id),
        )

    ScheduledTaskStore(db_path=str(db_path))
    TaskStateManager(db_path=str(db_path))

    with sqlite3.connect(db_path) as conn:
        raws = [
            conn.execute(
                "SELECT config_json FROM agent_scheduled_tasks WHERE id = ?", (sid,)
            ).fetchone()[0],
            conn.execute(
                "SELECT config_json FROM agent_tasks WHERE id = ?", (task_id,)
            ).fetchone()[0],
        ]
    assert all("legacy-secret" not in raw for raw in raws)
    assert all('"max_tokens": 99' in raw for raw in raws)
