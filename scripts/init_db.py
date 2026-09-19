"""Connect to PostgreSQL and initialize the RAG storage schema.

Run: uv run python scripts/init_db.py
"""

from __future__ import annotations

from dotenv import load_dotenv

from llm_wiki.database import DatabaseSettings, connect_database, initialize_schema


def main() -> None:
    load_dotenv()
    settings = DatabaseSettings.from_env()

    with connect_database(settings) as connection:
        initialize_schema(connection)

        database, user, postgres_version, vector_version = connection.execute(
            """
            SELECT
                current_database(),
                current_user,
                current_setting('server_version'),
                (SELECT extversion FROM pg_extension WHERE extname = 'vector')
            """
        ).fetchone()
        tables = connection.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('documents', 'chunks')
            ORDER BY tablename
            """
        ).fetchall()
        embedding_type = connection.execute(
            """
            SELECT format_type(attribute.atttypid, attribute.atttypmod)
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'chunks'::regclass
              AND attribute.attname = 'embedding'
              AND NOT attribute.attisdropped
            """
        ).fetchone()[0]

    print(f"database={database}")
    print(f"user={user}")
    print(f"postgres_version={postgres_version}")
    print(f"vector_version={vector_version}")
    print(f"tables={','.join(table for (table,) in tables)}")
    print(f"embedding_type={embedding_type}")


if __name__ == "__main__":
    main()
