# DayAnchor
Daily Task app for both personal and clinic responsibilities with AI incorporation  

## Postgres Setup (Minimal)

Use these two environment variables:

1. `DATABASE_URL` (required)
2. `DATABASE_SSLMODE` (recommended, usually `require`)

Example:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
DATABASE_SSLMODE=require
```

Notes:

- The app also accepts `POSTGRES_URL`, `POSTGRESQL_URL`, or `DB_URL` as URL variable names.
- Legacy split variables (`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`) still work as a fallback.
