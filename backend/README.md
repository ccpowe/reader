# Reader backend

The `reader` package: one FastAPI API and one background worker sharing a
PostgreSQL database. The API serves the app (accounts, subscriptions, feed,
saved items, boards, translations) and an MCP endpoint for AI assistants; the
worker scans sources, translates titles in the background and runs the daily
clean-up. No hosted auth, storage bucket or mail service is needed.

How the parts fit together, who owns which data and the main flows are in
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md). Commands and conventions for
working on the code are in [AGENTS.md](AGENTS.md); deployment is in
[docs/deployment.md](../docs/deployment.md) and
[docs/docker-deployment.md](../docs/docker-deployment.md).

## Local setup

1. Copy `.env.example` to `.env` (mode 600). Set `APP_SERVER_ID`, and replace
   the example connection token and JWT secret with independent random values.
   Every `APP_*` name matches a field of `src/reader/core/settings.py`.
2. Start PostgreSQL: `docker compose -f compose.postgres.yml up -d`.
   `APP_DATABASE_URL` must match `POSTGRES_USER/PASSWORD/DB/PORT`; the database
   listens on loopback only.
3. `uv sync --dev`, then `uv run alembic upgrade head`.
4. Run the API and the worker in separate processes:

   ```bash
   uv run uvicorn reader.main:create_app --factory --host 127.0.0.1 --port 8000
   uv run reader-worker            # --once runs every job one time and exits
   ```

5. Check `GET /health`, `GET /ready` (configuration and schema revision) and
   `GET /worker-ready` (the worker's loop heartbeats).

## Connecting and accounts

- `GET /.well-known/reader.json` (with the connection token) tells the app the
  server ID and the API base URL. Behind a reverse proxy, set
  `APP_PUBLIC_API_BASE_URL` to the external base, including any path prefix.
- Every request except health checks, the OpenAPI document, avatars and relayed
  source icons needs the deployment's connection token in
  `X-Reader-Server-Token`. Signed-in routes also need `Authorization: Bearer`.
- Registration needs no e-mail verification. Access tokens are 15-minute JWTs;
  refresh tokens are single-use, and reusing one ends that device's session.
  Changing the password ends the other devices' sessions.
- Forgotten password: an operator runs `uv run reader-admin users reset-password
  <email>` (the new password is typed, never passed as an argument).

## Translation

One translation service (`src/reader/translation/service.py`) serves both
`POST /translations` (titles the feed lacks, board titles and descriptions, web
page segments, captions) and the worker's background title loop. Results are
cached in `translations`, shared by all users, keyed by purpose, text,
target language and engine fingerprint.

- Engines: `deepseek-v4-flash` (`APP_DEEPSEEK_API_KEY`),
  `openrouter-minimax-m3` (`APP_OPENROUTER_API_KEY`), `codex-subscription` (a
  dedicated login imported with `reader-admin codex-auth import`, see the
  [deployment guide](../docs/deployment.md#codex-订阅翻译)), or `disabled`.
  `APP_TRANSLATION_DEFAULT_ENGINE_ID` picks the default; users may choose
  another available engine.
- An engine without credentials is shown as unavailable; translation requests
  for it return `translation_unavailable` and nothing else is affected.
- `APP_TRANSLATION_CHARS_PER_MINUTE` and `APP_TRANSLATION_CHARS_PER_DAY` cap
  the characters the whole deployment sends to models (cache hits are free;
  `0` turns a limit off). `reader-admin translation usage` shows what has been
  used.

## Sources and boards

- Subscriptions: RSS/Atom, websites (Reader finds the site's RSS/Atom feed;
  sites without one are not supported), YouTube channels (Data API with
  `APP_YOUTUBE_DATA_API_KEY` and quota, otherwise the public Atom feed), X
  accounts (through the optional [Scweet service](../services/README.md)) and
  subreddits (public RSS).
- The worker scans due sources every `APP_WORKER_POLL_INTERVAL_SECONDS`. A first
  scan reads one page as a baseline; later scans stop at the first known item.
  The daily clean-up keeps the newest `APP_ARTICLES_KEPT_PER_SOURCE` articles
  per source (saved ones count too) and every older article someone has saved.
- Boards (Hacker News, GitHub Trending, Product Hunt, Reddit; TechTwitter's fetcher is kept
  but the board is hidden in the app and MCP, since its API now blocks servers) are
  fetched by the API when someone opens an expired one; there is no background
  refresh. Product Hunt needs `APP_PRODUCT_HUNT_TOKEN`.
- Requests to user-supplied public sources go through
  `src/reader/ingestion/url_safety.py`: public destinations only, bounded reads.
  The operator-configured private Scweet service is exempt from the
  public-destination check; its responses are still read with a size limit.
  `APP_TRUST_PROXY_FAKE_IP` exists only for a local fake-IP proxy and must stay
  off in production.

## MCP

`/mcp` (Streamable HTTP) runs inside the API. An agent sends the connection
token and a personal MCP token, issued per account with
`uv run reader-admin mcp-token create <email>` (shown once; `revoke` removes
it). The tools and a client configuration example are in the
[README](../README.md#通过-mcp-交给-ai-阅读).

## Tests

See [AGENTS.md](AGENTS.md#环境与命令): unit tests run with
`PYTHON_DOTENV_DISABLED=1 uv run pytest -q`; PostgreSQL tests run against a
throwaway container with `bash scripts/test-postgres-migrations.sh`.
