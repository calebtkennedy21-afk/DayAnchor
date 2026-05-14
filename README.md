# DayAnchor
Daily Task app for both personal and clinic responsibilities with AI incorporation  

## Postgres Setup (Minimal)

Use one of these environment variables (either works):

1. `DATABASE_URL`
2. `DATABASE_PUBLIC_URL`

Example:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
```

Notes:

- No extra SSL variable is required.
- The app also accepts `POSTGRES_URL`, `POSTGRESQL_URL`, or `DB_URL` as URL variable names.
- Legacy split variables (`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`) still work as a fallback.
