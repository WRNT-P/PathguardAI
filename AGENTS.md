# AGENTS.md — PathGuard AI

## Agent Roles

### Codex
- Generate boilerplate, skeletons, and repetitive code
- Fill empty scaffold files (models, schemas, CRUD)
- Write `requirements.txt`, config files

### Claude
- Review and fix Codex output
- Implement AI module logic (modules 1–5)
- Debug errors and handle edge cases
- Architecture decisions

## Database Split
| Data | Database |
|------|----------|
| GPS live position, alerts, chat | Firebase Realtime DB |
| GPS history (30 days), Behavioral Profile, Risk Score, AI training data | PostgreSQL |

## Workflow
1. Codex generates → paste output to Claude for review
2. Claude fixes/approves → save to file
3. Repeat per module
