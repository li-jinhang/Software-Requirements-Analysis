"""Upgrade both live workspaces and offline SQLite backup copies."""


def migrate_document_library(execute):
    columns = {row[1] for row in execute("PRAGMA table_info(documents)")}
    if "is_library" not in columns:
        execute("ALTER TABLE documents ADD COLUMN is_library BOOLEAN NOT NULL DEFAULT 0")
    if "library_id" not in columns:
        execute(
            "ALTER TABLE documents ADD COLUMN library_id INTEGER "
            "REFERENCES documents(id) ON DELETE SET NULL"
        )
    execute("CREATE INDEX IF NOT EXISTS ix_documents_library_id ON documents(library_id)")
