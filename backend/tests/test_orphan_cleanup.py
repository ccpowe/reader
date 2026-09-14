from sqlalchemy.dialects import postgresql

from app.workers.cleanup import orphan_source_delete_statement


def test_orphan_cleanup_requires_no_subscription_or_saved_entry() -> None:
    statement = orphan_source_delete_statement(limit=25)
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "DELETE FROM feed_sources" in sql
    assert "source_subscriptions" in sql
    assert "user_saved_contents" in sql
    assert sql.count("NOT (EXISTS") >= 4
    assert "LIMIT %(param_1)s" in sql
