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
    if "library_setup" not in columns:
        execute("ALTER TABLE documents ADD COLUMN library_setup JSON")
    execute("CREATE INDEX IF NOT EXISTS ix_documents_library_id ON documents(library_id)")
    # Promote the seeded course in place so its graph, notes, and sessions keep their IDs.
    execute("""
        UPDATE documents SET is_library = 1
        WHERE is_library = 0 AND library_id IS NULL AND parse_status = 'completed'
          AND title = '机器学习基础：梯度下降与过拟合'
          AND (
            ('/' || replace(file_path, char(92), '/')) GLOB '*/demo-course.md'
            OR ('/' || replace(file_path, char(92), '/')) GLOB '*/demo-course-*.md'
          )
    """)
