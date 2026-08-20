# Podcast Intelligence frontend

React, Vite, and TypeScript presentation layer for the local FastAPI application.

```bash
npm ci
npm run api:check
npm run format:check
npm run lint
npm run typecheck
npm run test:coverage
npm run build
npm run test:e2e
```

`npm run dev` starts Vite and proxies `/api` to `http://127.0.0.1:8000`. The production build is
written into the Python package and served by `uv run podcast-intelligence-web`.
