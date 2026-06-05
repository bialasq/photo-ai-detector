#!/usr/bin/env bash
# Setup GitHub labels (idempotent).
set -euo pipefail
REPO="bialasqq/photo-ai-detector"

gh label create "P0" --color "d73a4a" --description "Must have for v1.0.0" --repo "$REPO" --force || true
gh label create "P1" --color "fbca04" --description "Should have for v1.0.0" --repo "$REPO" --force || true
gh label create "P2" --color "0e8a16" --description "Nice to have / v1.1+" --repo "$REPO" --force || true
gh label create "phase-0" --color "c5def5" --description "Scope & setup" --repo "$REPO" --force || true
gh label create "phase-1" --color "bfdadc" --description "Stability + Security + Data" --repo "$REPO" --force || true
gh label create "phase-2" --color "d4c5f9" --description "Performance & Scaling" --repo "$REPO" --force || true
gh label create "phase-3" --color "c2e0c6" --description "Testing, Security Audit, CI/CD" --repo "$REPO" --force || true
gh label create "phase-4" --color "fef2c0" --description "Deployment & Release" --repo "$REPO" --force || true
gh label create "owner-grzesiek" --color "5319e7" --description "Grzesiek (Cursor-assisted)" --repo "$REPO" --force || true
gh label create "owner-luq" --color "0052cc" --description "Luq (manual senior)" --repo "$REPO" --force || true
gh label create "owner-both" --color "e99695" --description "Pair task" --repo "$REPO" --force || true
gh label create "backend" --color "1d76db" --description "Python FastAPI" --repo "$REPO" --force || true
gh label create "frontend" --color "0e8a16" --description "React TypeScript" --repo "$REPO" --force || true
gh label create "tauri" --color "a370ff" --description "Tauri Rust" --repo "$REPO" --force || true
gh label create "devops" --color "ededed" --description "CI/CD, scripts" --repo "$REPO" --force || true
gh label create "security" --color "b60205" --description "Security" --repo "$REPO" --force || true
gh label create "docs" --color "ffffff" --description "Documentation" --repo "$REPO" --force || true
gh label create "tests" --color "d4c5f9" --description "Tests" --repo "$REPO" --force || true
gh label create "performance" --color "fbca04" --description "Performance" --repo "$REPO" --force || true
gh label create "ml" --color "1d76db" --description "DeepFace / FAISS / clustering" --repo "$REPO" --force || true
gh label create "database" --color "5319e7" --description "SQLite schema, migrations, indexes" --repo "$REPO" --force || true
gh label create "api" --color "0e8a16" --description "API design, versioning" --repo "$REPO" --force || true
gh label create "stability" --color "c2e0c6" --description "Error handling, recovery" --repo "$REPO" --force || true
gh label create "observability" --color "fbca04" --description "Logging, monitoring" --repo "$REPO" --force || true
gh label create "ux" --color "ff7619" --description "User experience" --repo "$REPO" --force || true
gh label create "release" --color "d73a4a" --description "Release process" --repo "$REPO" --force || true
gh label create "blocker" --color "b60205" --description "Blocks release" --repo "$REPO" --force || true
gh label create "compliance" --color "0052cc" --description "Licensing, GDPR" --repo "$REPO" --force || true
gh label create "audit" --color "c5def5" --description "Audit" --repo "$REPO" --force || true
gh label create "qa" --color "0e8a16" --description "QA" --repo "$REPO" --force || true
gh label create "support" --color "c2e0c6" --description "Support" --repo "$REPO" --force || true
gh label create "process" --color "ededed" --description "Process / workflow" --repo "$REPO" --force || true
gh label create "scope" --color "fef2c0" --description "Scope freeze" --repo "$REPO" --force || true
gh label create "setup" --color "ededed" --description "Initial setup" --repo "$REPO" --force || true
gh label create "feature" --color "0e8a16" --description "User-facing feature" --repo "$REPO" --force || true
gh label create "maintenance" --color "ededed" --description "Maintenance task" --repo "$REPO" --force || true
gh label create "e2e" --color "d4c5f9" --description "End-to-end tests" --repo "$REPO" --force || true
gh label create "quality" --color "c2e0c6" --description "Code quality" --repo "$REPO" --force || true
gh label create "data" --color "5319e7" --description "Data integrity" --repo "$REPO" --force || true