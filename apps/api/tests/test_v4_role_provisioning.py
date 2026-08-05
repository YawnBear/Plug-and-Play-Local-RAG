from pathlib import Path


def test_role_provisioning_has_no_embedded_secret_and_hardens_every_role() -> None:
    script = (
        Path(__file__).parents[3] / "ops" / "security" / "provision-postgres-roles.ps1"
    ).read_text(encoding="utf-8")
    assert "Read-Host" in script
    assert "-AsSecureString" in script
    assert "rag_owner NOLOGIN" in script
    assert "rag_migrator LOGIN NOINHERIT" in script
    assert "rag_api LOGIN NOINHERIT" in script
    assert "rag_worker LOGIN NOINHERIT" in script
    assert "rag_maintenance LOGIN NOINHERIT" in script
    assert "rag_backup NOLOGIN NOINHERIT" in script
    assert "ALTER ROLE rag_backup PASSWORD NULL" in script
    prompted_roles = script[
        script.index("$roleNames = @(") : script.index("$passwords = @{}")
    ]
    assert "rag_backup" not in prompted_roles
    assert "NOBYPASSRLS" in script
    assert "BYPASSRLS" in script
    assert "default_transaction_read_only = on" in script
    assert "GRANT rag_owner TO rag_migrator" in script
    assert "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC" in script
    assert "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO rag_owner" in script
    assert "public.cosine_distance(vector, vector) TO rag_api" in script
    assert "extension function ACL hardening failed" in script
    assert "CREATE EXTENSION IF NOT EXISTS vector" in script
    assert "GRANT USAGE, CREATE ON SCHEMA public TO rag_owner;" in script
    assert (
        "GRANT USAGE ON SCHEMA public TO rag_migrator, rag_api, rag_worker," in script
    )
    assert "rag_maintenance, rag_backup;" in script
    assert "Remove-Item -LiteralPath $temporarySql -Force" in script
    assert "password=" not in script.lower()
