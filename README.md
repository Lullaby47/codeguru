## CodeGuru

### Tests and guardrails

- **Auth/web route separation**: Browsers must only navigate to web routes such as `/login` and `/signup`.  
  Direct links to API endpoints like `/auth/login` or `/auth/signup` are forbidden in templates.

- To enforce this, run:

```bash
pytest tests/test_no_auth_links.py
```

You can add this command to CI or a pre-commit hook to prevent accidentally introducing bad links.


### Database configuration

- In **production (Railway)** you should use **Postgres** via the `DATABASE_URL` environment variable
  (Railway usually provides this automatically). The app will detect a `postgres://...` URL and normalize
  it to a SQLAlchemy-compatible `postgresql+psycopg2://` URL.
- For **local development**, if `DATABASE_URL` is not set the app falls back to a SQLite file
  at `codeguru.db` in the project root.
- Alembic migrations use the same `DATABASE_URL` logic, so running

```bash
alembic upgrade head
```

  will target the same database backend that the app uses.

