from pathlib import Path

import yaml


COMPOSE = Path(__file__).resolve().parents[2] / "RenuxServer" / "docker-compose.yml"


def test_compose_waits_for_real_readiness_through_the_service_graph():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert services["api"]["depends_on"] == {
        "db": {"condition": "service_healthy"},
        "rag-service": {"condition": "service_healthy"},
    }
    assert services["rag-service"]["depends_on"] == {"redis": {"condition": "service_healthy"}}
    assert services["nginx"]["depends_on"] == {"api": {"condition": "service_healthy"}}
    assert services["redis"]["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
    assert "pg_isready" in " ".join(services["db"]["healthcheck"]["test"])
    for service in ("rag-service", "api"):
        probe = " ".join(services[service]["healthcheck"]["test"])
        assert "/ready" in probe
        assert "/health" not in probe
    assert all(service["restart"] == "unless-stopped" for service in services.values())


def test_missing_rag_database_mount_does_not_create_a_directory():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    database = next(
        mount for mount in services["rag-service"]["volumes"]
        if isinstance(mount, dict) and mount.get("target") == "/app/rag_database.db"
    )
    assert database["type"] == "bind"
    assert database["source"] == "../RAG/rag_database.db"
    assert database["bind"]["create_host_path"] is False
