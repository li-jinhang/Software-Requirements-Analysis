"""Add review scheduling to existing workspaces and offline export copies."""
def migrate_reviews(execute):
    definitions = {
        "sessions": {"mode": "TEXT NOT NULL DEFAULT 'teach'", "target_item_ids": "JSON NOT NULL DEFAULT '[]'",
                     "review_cursor": "INTEGER NOT NULL DEFAULT 0", "review_completed": "BOOLEAN NOT NULL DEFAULT 0"},
        "cognitive_items": {"review_stage": "INTEGER NOT NULL DEFAULT 0", "review_count": "INTEGER NOT NULL DEFAULT 0",
                            "skip_count": "INTEGER NOT NULL DEFAULT 0", "next_review_at": "DATETIME",
                            "last_reviewed_at": "DATETIME"},
    }
    for table, fields in definitions.items():
        existing = {row[1] for row in execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue
        for name, definition in fields.items():
            if name not in existing:
                execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        if table == "cognitive_items" and "next_review_at" not in existing:
            execute("""UPDATE cognitive_items SET last_reviewed_at=updated_at,
                       review_stage=CASE status WHEN 'mastered' THEN 1 ELSE 0 END,
                       next_review_at=datetime(updated_at, CASE status WHEN 'mastered' THEN '+1 day'
                           WHEN 'partial' THEN '+12 hours' ELSE '+10 minutes' END)
                       WHERE status != 'untested'""")
