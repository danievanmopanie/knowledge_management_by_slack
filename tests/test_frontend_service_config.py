from pathlib import Path


SERVICE = Path("deploy/systemd/frontend-support-agent.service")


def test_frontend_service_uses_dedicated_code_checkout_and_shared_knowledge_data():
    text = SERVICE.read_text(encoding="utf-8")

    assert "WorkingDirectory=/home/danieungerer/knowledge_management_by_slack_frontend_runtime" in text
    assert "EnvironmentFile=/home/danieungerer/knowledge_management_by_slack/.env" in text
    assert "ExecStart=/usr/bin/env " in text
    assert (
        "VECTORSTORE_PATH=/home/danieungerer/knowledge_management_by_slack/data/vectorstore"
        in text
    )
    assert "INCIDENTS_PATH=/home/danieungerer/knowledge_management_by_slack/data/incidents" in text
    assert "PLATFORM_DB_PATH=/home/danieungerer/knowledge_management_by_slack/data/platform.db" in text
    assert (
        "/home/danieungerer/knowledge_management_by_slack_frontend_runtime/.venv/bin/python "
        "-m src.bot.frontend_support_app"
        in text
    )


def test_env_example_documents_dedicated_frontend_support_tokens():
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "FRONTEND_SUPPORT_SLACK_BOT_TOKEN=" in text
    assert "FRONTEND_SUPPORT_SLACK_APP_TOKEN=" in text
