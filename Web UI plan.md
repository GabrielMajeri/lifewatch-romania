# Plan: Web Interface for Lifewatch Romania

**TL;DR:** Extend the existing Python project with a **FastAPI** backend, add a **Next.js** (React + TypeScript) frontend, and wire them together in `compose.yaml`. This gives you a public-facing dashboard, an internal admin panel, real-time updates, and alerting — all self-hosted.

---

## Context

- PostgreSQL 18 + TimescaleDB hypertables; SQLAlchemy 2 + psycopg3, Python 3.14+
- 9 data models: Stations, MeteorologicalXxxMeasurement (20s/30s/60s), EddyCovarianceMeasurement, IngestedFile
- Existing: Typer CLI for ingestion; no web layer yet
- Goals: internal admin + public read-only, advanced queries, alerting, real-time, self-hosted via compose.yaml
- Team: React + TypeScript experienced; Python also fine

---

## Backend: FastAPI (Python)

Add to the existing `pyproject.toml` as extra dependencies — no separate repo needed.

- **FastAPI + Uvicorn**: async REST API, auto-generates OpenAPI/Swagger docs for free. Pairs perfectly with the existing SQLAlchemy 2 async + psycopg3 stack.
- **[SQLAdmin](https://aminalaee.dev/sqladmin/)**: drop-in admin panel (inspired by Django Admin) that introspects your existing SQLAlchemy models. Covers the "internal admin" use case with almost no code.
- **WebSockets / SSE**: FastAPI has native support; SSE is the simpler choice for pushing new measurements to the dashboard.
- **APScheduler** (or FastAPI BackgroundTasks): for alerting — schedule threshold checks on recent measurements and push email/webhook notifications.
- **JWT auth middleware**: protect write/admin routes; leave read-only data routes open for the public view.

---

## Frontend: Next.js 15 App Router (TypeScript)

Separate service in `compose.yaml`. Your team is already proficient in React/TS, so this is the natural choice.

- **Next.js App Router**: SSR for public-facing pages (good for SEO/sharing), client components for interactive dashboards.
- **TanStack Query**: server state management, caching, polling intervals — the idiomatic way to consume a REST API from React.
- **[Apache ECharts](https://echarts.apache.org/) (`echarts-for-react`)**: best-in-class for large scientific time-series. Has built-in data zoom, brush selection, multiple y-axes, and handles the density of 20s/30s/60s measurements without needing decimation on the frontend.
- **Tailwind CSS + shadcn/ui**: composable, accessible components; fast to build station selector dropdowns, date range pickers, filter panels.
- **Auth.js (NextAuth v5)**: handles login for internal users; the public read-only view requires no auth at all.

---

## Deployment: extend `compose.yaml`

Add three new services to `compose.yaml`:

| Service | Image/Build        | Role                                            |
|---------|--------------------|-------------------------------------------------|
| `api`   | Python (uvicorn)   | FastAPI backend                                 |
| `web`   | Node build / SSR   | Next.js frontend                                |
| `proxy` | `nginx` or traefik | Reverse proxy, routes `/api/` → FastAPI, `/` → Next.js |

---

## Architecture

```
Browser
  │
  ▼
nginx (reverse proxy)
  ├── /          → Next.js (public dashboard + admin UI)
  └── /api/      → FastAPI
                    ├── REST endpoints (stations, measurements, ingestion status)
                    ├── SSE endpoint (real-time measurement stream)
                    ├── /admin/ → SQLAdmin panel
                    └── APScheduler (alerting background tasks)
                         │
                         ▼
              TimescaleDB (PostgreSQL 18)
              (existing hypertables)
```

---

## Further Considerations

1. **Grafana as an optional companion**: TimescaleDB has a native Grafana plugin with built-in alerting (mature, threshold-based, supports PagerDuty/Slack). You could run Grafana alongside for the research/operations team (internal only) and keep the custom Next.js app as the polished public face — they share the same DB, no duplication.

2. **Real-time strategy**: SSE is sufficient for "push new data to dashboard" and is much simpler to implement and proxy than WebSockets. WebSockets would only add value if you need bidirectional communication (e.g., live control of sensors), which doesn't seem needed here.

3. **Alerting scope**: keeping alerting inside the FastAPI process (APScheduler + email) is fine for low-frequency checks (e.g., "no data ingested in 6h"). If you grow toward complex multi-condition alerting, consider extracting it to a dedicated Celery or ARQ worker later — but that's a future concern.
