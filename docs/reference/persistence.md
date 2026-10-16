# Database and migration details

Read this reference when the task touches these owners. Start with the shared
[engineering rules](../engineering-rules.md) and [task index](../index.md).

## Database & migrations

- Single SQLite file. Dev: `./data/jobtracker.db`. Frozen app:
  `~/Library/Application Support/JobTracker/jobtracker.db`.
- `Database._init_db()` remains the compatibility entry point; schema work lives
  in [SchemaManager](../../jobtracker/core/persistence/schema.py). Migrations use
  additive, idempotent `ALTER TABLE` blocks guarded by `_column_exists`.
  Preserve their order and that pattern; this extraction is not a schema redesign.
- **Before any non-additive / hard-to-reverse schema change** (e.g. renaming the
  legacy `tasks` table — which actually stores *subjects* — to `subjects`):
  - Explain the migration in the PR/description and in code comments.
  - Make it reversible/safe (back up or keep old columns) and test it on a copy.
  - Do **not** do the `tasks` → `subjects` rename casually; it touches every
    query and the import legacy-key handling.
- `last_active_at` (sessions) is a heartbeat timestamp updated ~once/minute while
  active. One UPDATE, no history rows. Keep it cheap.
- Tables: `tasks` (subjects), `sessions`, `todo_tasks` (goals), `settings`,
  `milestones` (FK to `todo_tasks`), `goal_templates`, `weight_measurements`, and
  `weight_uid_aliases`. The `tasks`/`todo_tasks`/`TodoTask` names are legacy and stay for compatibility;
  user-facing terminology is **Subjects** and **Goals**. The **API uses the clean
  names** (`subjects`, `goals`) and maps to the legacy tables — that is how we get
  good nomenclature without the risky table rename.
- Additive columns currently added by `_init_db()`: `tasks.sort_order`,
  `tasks.is_archived`, `sessions.last_active_at`, `todo_tasks.template_id`,
  `todo_tasks.is_focused` (weekly focus, 0/1),
  `goal_templates.recurrence_day` (weekday 1–7 for weekly, day-of-month for
  monthly), `uid` on shared row tables, and `sessions.device_id`. All
  guarded by `_column_exists`.
- **A column added to a synced table needs a matching field on its dataclass.**
  Rows are read as `Model(**dict(row))` over `SELECT *`
  ([database.py](../../jobtracker/core/database.py)), so a new column with no field on
  the model raises `TypeError` on *every* read. Add it to
  [models.py](../../jobtracker/core/models.py) in the same change.
- **Downgrade hazard:** once a database has been opened by a build that has the
  `uid` columns, an older JobTracker build cannot read it (same `SELECT *` reason).
  Use the installer's recorded pre-update backups to locate a compatible copy;
  never restore one over live data merely to roll back application code.
- The **only** non-additive migration is the `sessions.note` DROP (needs SQLite
  ≥ 3.35; older versions leave the column in place and it is simply unused).
  Don't add a second one casually.
