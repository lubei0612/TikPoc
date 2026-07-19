from pathlib import Path


MIGRATIONS = Path("supabase/migrations")


def test_supabase_migrations_keep_permissions_inside_tikpoc_tables() -> None:
    sql = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
    ).lower()

    assert "all tables in schema public" not in sql
    assert "revoke all on table" in sql
    assert "public.tikpoc_accounts" in sql
    assert "grant select, insert, update, delete on table" in sql


def test_supabase_migrations_protect_import_health_and_lead_invariants() -> None:
    sql = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(MIGRATIONS.glob("*.sql"))
    ).lower()

    assert "import_state" in sql
    assert "foreign key (account_id, device_id)" in sql
    assert "tikpoc_lead_stage_monotonic" in sql
    assert "stage in (" in sql
