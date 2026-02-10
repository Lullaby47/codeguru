## CodeGuru

### Tests and guardrails

- **Auth/web route separation**: Browsers must only navigate to web routes such as `/login` and `/signup`.  
  Direct links to API endpoints like `/auth/login` or `/auth/signup` are forbidden in templates.

- To enforce this, run:

```bash
pytest tests/test_no_auth_links.py
```

You can add this command to CI or a pre-commit hook to prevent accidentally introducing bad links.


