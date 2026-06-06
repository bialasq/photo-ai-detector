-- Task 2.2.3: persist DBSCAN quality metrics after each clustering run.

CREATE TABLE IF NOT EXISTS cluster_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL DEFAULT (datetime('now')),
    silhouette REAL,
    db_score REAL,
    n_clusters INTEGER NOT NULL DEFAULT 0,
    eps REAL NOT NULL DEFAULT 0.35
);

CREATE INDEX IF NOT EXISTS idx_cluster_health_run_at
    ON cluster_health (run_at DESC);
