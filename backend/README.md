# Reader backend

FastAPI backend using PostgreSQL for business data, Reader accounts, refresh
sessions and avatars. Reader API owns authentication and personal reading state;
Worker owns shared ingestion. No hosted Auth, Storage bucket or SMTP is required.

## Local setup

1. Copy `.env.example` to `.env`; set a stable server ID and replace example secrets.
2. Start PostgreSQL: `docker compose -f compose.postgres.yml up -d`.
3. Run `uv sync --dev`, then `uv run alembic upgrade head`.
4. Start `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000` and
   `uv run reader-worker` in separate processes.

APP_DATABASE_URL must match POSTGRES_USER/PASSWORD/DB/PORT. PostgreSQL stays on
loopback. See [deployment instructions](../docs/deployment.md) for migration.

## Reader accounts and profiles

`/v1/auth` owns registration, login, refresh, logout and account changes.
Registration immediately creates a usable session without verification email,
resend page or callback URL. Password/email changes require the current password.
Administrators reset forgotten passwords using `reader-admin users reset-password <email>`;
the command prompts without placing the new password in shell history.

New hashes use Argon2. Imported bcrypt hashes remain usable and upgrade after login.
Access JWTs use APP_AUTH_JWT_SECRET; opaque refresh tokens are hashed in PostgreSQL
and rotated transactionally. Logout, password changes and refresh reuse revoke
applicable sessions. The server token and JWT signing secret are independent.

`GET/PATCH /v1/me/profile` owns nickname/avatar metadata. The account's current
email is authoritative. `PUT /v1/me/profile/avatar` accepts raw JPEG/PNG/WebP up to
5 MiB, verifies its contents and stores it in PostgreSQL. Random versioned
`/avatars/{user_id}/{version}` URLs serve images read-only.

## Reader discovery and server token

`GET /.well-known/reader.json` and every `/v1` request require
`X-Reader-Server-Token` matching APP_SERVER_ACCESS_TOKEN. Authenticated routes
additionally require a Reader Bearer token. OPTIONS and liveness remain accessible.
Never put credentials in URLs, public metadata, logs or build configuration.

Discovery returns exactly `protocol_version: 2`, `server_id` and `api_base_url`.
APP_PUBLIC_API_BASE_URL is the canonical external API base; otherwise trusted
ASGI request/root_path information supplies it. Configure proxy trust explicitly.
Credential-bearing requests cannot follow redirects; discovery's API base must
remain on the same origin. Enter the final address in the mobile client.

The connection form asks for the URL and token, with per-server secret storage
separate from public metadata. Old Supabase sessions require reconnecting and
logging in again with the preserved password. OpenAPI and the generated mobile
client define exact request shapes.

### Protocol and network matrix

| Client | HTTP release target | HTTPS/TLS | Browser policy |
| --- | --- | --- | --- |
| Android | Supported for operator-controlled targets; the release build opts into cleartext traffic. | Use a certificate trusted by the device. | Native fetch is not subject to browser CORS. |
| iOS | Supported for operator-controlled targets; the release build opts into ATS arbitrary loads. | Use a certificate trusted by the device. | Native fetch is not subject to browser CORS. |
| Web | Only when the page itself is HTTP; an HTTPS page cannot fetch an HTTP server. | Use valid HTTPS and matching certificate/hostname. | Configure exact CORS origins, methods, headers, and OPTIONS preflight. |

This HTTP support widens the release attack surface. Prefer HTTPS outside a
trusted development LAN and review the cleartext/ATS exceptions before store
submission. Web mixed-content and CORS restrictions are enforced by the
browser and cannot be disabled by Reader.

### Localhost, prefixes, and troubleshooting

`localhost` means the device running the app, not necessarily the development
computer. The iOS Simulator can use `http://127.0.0.1:8000`; the default
Android Emulator uses `http://10.0.2.2:8000`; a physical device needs the
computer's LAN address (for example `http://192.168.1.20:8000`) or a tunnel.
Ensure the firewall allows the port. Bracket IPv6 literals, such as
`http://[::1]:8000`, and keep query strings, fragments, and userinfo out of the
base URL.

For a reverse proxy mounted at `/reader`, either set
`APP_PUBLIC_API_BASE_URL=https://host.example/reader` or configure trusted
forwarded headers and ASGI `root_path`. The discovery response, `/v1` routes,
avatar endpoint, translation calls, and WebView proxy URLs must all retain the
same prefix. A 404 under the prefix usually means the proxy stripped or added
the prefix twice.

For Web failures, inspect the browser Network panel for an `OPTIONS` request;
add the exact app origin to `APP_CORS_ORIGINS`, allow the requested
`Authorization`, `Content-Type` and `X-Reader-Server-Token` headers.
OPTIONS is unauthenticated; discovery itself requires the server token. A mixed-content error means the app page is HTTPS while
the requested base is HTTP. For TLS failures, install a trusted certificate
whose hostname matches the URL; do not “fix” this by disabling verification.
Configure the final URL directly; never forward credentials across redirects.

The unauthenticated `GET /health` endpoint is process liveness only. Deployments
must gate traffic on `GET /ready`, which checks required configuration, database
connectivity, and the minimum schema contract required by this build. Before
accepting traffic, startup allows up to thirty seconds for a cold PostgreSQL
connection to enter the pool and retries transient connection failures within
that budget. Warm probes retain a two-second deadline and a five-second
process-local single-flight cache. A failed warm-up does not crash the process:
startup completes with `/ready` unavailable. Expose `/ready` only to the
orchestrator or management network; public ingress should expose `/health` and
business routes, not the database-backed readiness endpoint. All business
endpoints require a Reader access token and the server token. Database and JWT
signing secrets never belong in the Expo application.

The separate unauthenticated `GET /worker-ready` endpoint reports whether every
required background loop is present, recent, and last completed successfully.
The required loops are source scanning, candidate processing, ranking snapshots,
orphan cleanup, and foreground/background durable translation. Realtime webpage
and caption translation belongs to the API coordinator and has no worker heartbeat.
Use it as the worker deployment's readiness/alerting signal; a non-200 response
contains stable loop names and error codes, not exception messages. Keep it on
the management network for the same reason as `/ready`.

## Local MCP bridge

Install optional dependencies with `uv sync --extra mcp` and launch `uv run --extra mcp reader-mcp` from a private AI-host environment. This stdio process connects to the existing authenticated API; it is not an unauthenticated HTTP `/mcp` endpoint and needs no direct database access. Docker API/Worker images do not install this optional client extra.

The six tools, host configuration, time/pagination semantics and provider limitations are documented in the [Reader MCP guide](../README.md#通过-mcp-交给-ai-阅读). Business endpoints and generated types remain part of the normal OpenAPI contract. The bridge does not automatically load `backend/.env`.

Product Hunt daily featured launches are optional: set `APP_PRODUCT_HUNT_TOKEN` privately on API and Worker, then restart both. Docker deployments use `.docker/inputs/product-hunt-token`, sealed and mounted through the normal `docker-init.sh up` flow. The authenticated `/v1/rankings/product_hunt` endpoint accepts `date=YYYY-MM-DD` (default: today in `America/Los_Angeles`); the App and existing MCP ranking tool accept historical dates. Queries revalidate against the official API after the shared refresh cooldown (default 60 seconds), and past dates never reuse a capture made before that day ended. The worker refreshes only today's date every 30 minutes. API `RANKING` order and scores reflect fetch time, not guaranteed historical closing results. See the [MCP guide](../README.md#通过-mcp-交给-ai-阅读) for limits and API terms.

## Layout

- `app/api`: HTTP routes and request/response schemas.
- `app/core`: settings, authentication, and application-wide wiring.
- `app/domain`: framework-independent business models.
- `app/ingestion`: source adapters and article extraction.
- `app/translation`: provider-independent translation contracts and adapters.
- `app/storage`: database and object-storage integration.
- `app/workers`: scheduled source synchronisation and asynchronous jobs.

## Translation runtime

Migration `20260803_10` replaces the development translation cache with
demand-driven `translation_work` and immutable `translation_artifacts`. Feed,
saved, and article reads return original text immediately, create missing work
in one batch, and let the mobile client merge compact completion updates. The
application default is selected by a stable engine ID:

```env
APP_TRANSLATION_DEFAULT_ENGINE_ID=deepseek-v4-flash
APP_TRANSLATION_DEFAULT_TARGET_LOCALE=zh-CN
APP_TRANSLATION_PROMPT_VERSION=v2-caption-context
APP_TRANSLATION_BATCH_SIZE=50
APP_TRANSLATION_FOREGROUND_BATCH_SIZE=12
APP_TRANSLATION_BACKGROUND_BATCH_SIZE=24
APP_TRANSLATION_LEASE_SECONDS=120
APP_TRANSLATION_MAX_ATTEMPTS=8
APP_TRANSLATION_CONTEXT_CACHE_SECONDS=30
APP_TRANSLATION_INTERACTIVE_TIMEOUT_SECONDS=8
APP_TRANSLATION_INTERACTIVE_MAX_ITEMS_PER_BATCH=12
APP_TRANSLATION_INTERACTIVE_MAX_CHARS_PER_BATCH=5000
APP_TRANSLATION_INTERACTIVE_MAX_CONCURRENCY=2
APP_TRANSLATION_REALTIME_WAIT_SECONDS=5
APP_TRANSLATION_REALTIME_MAX_CONCURRENCY=16
APP_TRANSLATION_IDLE_POLL_SECONDS=30
APP_TRANSLATION_LISTENER_FALLBACK_POLL_SECONDS=2
APP_TRANSLATION_PROVIDER_MAX_CONCURRENCY=4
APP_TRANSLATION_LLM_MAX_ITEMS_PER_REQUEST=24
APP_TRANSLATION_LLM_MAX_CHARS_PER_REQUEST=12000
APP_TRANSLATION_QUOTA_REQUESTS_PER_MINUTE=30
APP_TRANSLATION_QUOTA_USER_MISS_CHARS_PER_MINUTE=120000
APP_TRANSLATION_QUOTA_GLOBAL_MISS_CHARS_PER_MINUTE=500000
APP_DEEPSEEK_API_KEY=...
APP_DEEPSEEK_REQUEST_TIMEOUT_SECONDS=20
APP_DATABASE_POOL_SIZE=3
APP_DATABASE_POOL_MAX_OVERFLOW=0
APP_DATABASE_POOL_TIMEOUT_SECONDS=5
```

The `TranslationProvider` protocol is intentionally independent of LangChain.
`LangChainChatModelProvider` accepts an injected LangChain-compatible model, so
provider SDKs remain optional and user credentials never enter the mobile app.
See [the translation execution paths](../docs/ARCHITECTURE.md#翻译的三条执行路径)
for the current provider boundary and demand, work, artifact, and delivery semantics.

Migration `20260803_12` adds ranking descriptions, WebView segments, and
caption purposes. `POST /v1/translations/segments` is the bounded convergence
protocol used by ranking, reader, webpage, and caption surfaces. Ranking API
responses deliberately return original rows without waiting for translation;
the visible native window requests and merges translations afterward.
Webpage/caption requests use the process-local realtime coordinator: cache
misses map to shared BatchTask placeholders, do not create durable Work, and
complete provider results before the coordinator asynchronously publishes
their artifacts. Standard paragraph/ranking requests retain the existing
interactive and durable fallback behavior.
Migration `20260803_13` requeues work that older executors permanently failed
after exhausting attempts during a provider quota incident. Migration
`20260804_14` gives preferences, work, and artifacts stable engine identities,
and adds cancellation for unused private pending work. A task never changes
engine after creation; switching engines clears that user's WebView/caption
cache and prevents old in-flight results from publishing. There is no
automatic provider fallback. Migration `20260828_19` retires the legacy NMT
route, restores affected user preferences to the application default, and
cancels unfinished work for that retired route.

DeepSeek Flash (`deepseek-flash`, currently V4.1) is the application default and is connected through
LangChain. Put its key only in `backend/.env`:

```env
APP_TRANSLATION_DEFAULT_ENGINE_ID=deepseek-v4-flash
APP_DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-flash
APP_TRANSLATION_DEFAULT_TARGET_LOCALE=zh-CN
APP_TRANSLATION_FOREGROUND_BATCH_SIZE=12
APP_TRANSLATION_BACKGROUND_BATCH_SIZE=24
```

Migration `20260908_24` replaces the OpenRouter GLM choice with MiniMax M3,
moves explicit GLM preferences to MiniMax, and cancels unfinished GLM work.
Stop API and worker before `uv run alembic upgrade head`, then restart both.
Existing artifacts remain unchanged; new demand uses a distinct model fingerprint.
If the configured default was `openrouter-glm-5.3-flash`, change it to
`openrouter-minimax-m3` as part of deployment.

The managed catalog also includes OpenRouter, initially configured with
`minimax/minimax-m3`. Configure API and worker with the same credentials and
model values:

```env
APP_OPENROUTER_API_KEY=...
OPENROUTER_MODEL=minimax/minimax-m3
APP_OPENROUTER_API_BASE=https://openrouter.ai/api/v1
APP_OPENROUTER_REQUEST_TIMEOUT_SECONDS=20
```

`OPENROUTER_API_KEY` is also accepted; the `APP_` name takes precedence. The
existing translation preferences picker exposes the engine when its key is
configured. To make it the application default, set
`APP_TRANSLATION_DEFAULT_ENGINE_ID=openrouter-minimax-m3`; otherwise DeepSeek
remains the default. Keys must never be put in mobile configuration.

`DEEPSEEK_MODEL` and `OPENROUTER_MODEL` select the actual API model. Their
`APP_DEEPSEEK_MODEL` / `APP_OPENROUTER_MODEL` aliases are also accepted; `APP_`
takes precedence. Values are trimmed, nonempty, and at most 160 characters.
The App labels show the provider and configured model directly.

For future model updates, stop both services, edit the two model values in
`backend/.env`, then start both services:

```bash
sudo systemctl stop reader-api reader-worker
# Edit backend/.env: DEEPSEEK_MODEL=... and/or OPENROUTER_MODEL=...
sudo systemctl start reader-api reader-worker
```

No source edit, Git deployment, migration or APK build is needed for compatible
models. This is restart-loaded configuration, not hot reload. Verify a real
translation because model availability and JSON-mode support vary. A running
App may need a restart to refresh cached preferences and labels.

Historical engine IDs (`deepseek-v4-flash` / `openrouter-minimax-m3`) are opaque,
stable route keys retained for preference/cache compatibility; they no longer
specify the actual model. Do not change `APP_TRANSLATION_DEFAULT_ENGINE_ID`
when updating a model. Keeping the same models preserves existing fingerprints;
changing the actual model changes its fingerprint. Home/ranking titles and
excerpts can reuse successful artifacts from other models with matching text,
language, prompt and glossary versions. Exact current-model hits are preferred;
`title`/`ranking_title` and `excerpt`/`ranking_description` share compatible hits.
Worker cleanup runs on startup and hourly, cancelling at most 100 obsolete-model
pending or expired-lease jobs each cycle.

WebView and caption artifacts are user-scoped and expire exactly 60 minutes after
the successful result, without extending expiry on reads. The segments response
exposes `cache_expires_at` and `effective_engine_fingerprint`; preferences also
expose the effective fingerprint so clients detect configured-model changes.
Generation identities derived from preference updates prevent an A-to-B-to-A
switch from reviving an older in-flight result. Old shared caption artifacts are
never reused. Reads reject expired artifacts immediately; periodic cleanup
removes up to 1,000 eligible rows from each translation table per pass.

The same worker cleanup retains 500 unsaved contents per shared source using
existing `feed_sort_at` order, with any user's saved contents retained outside
that quota. Unsubscribed sources retain saved contents only. Content/media/state
and terminal candidate payloads are removed together, while ingestion checkpoints
remain intact. Current contents and ranking snapshots protect shared title and
excerpt translations across all model versions; translations and work without
such references are removed. These rules do not add a retention period for sync
logs or unassociated legacy reader paragraph/body artifacts.

OpenRouter uses the existing LangChain OpenAI-compatible chat adapter, JSON mode,
and excludes reasoning from the response (internal reasoning remains provider-controlled). SDK retries are disabled so Reader owns bounded retries.
Translation failures are returned as safe error codes and never trigger a
different model or non-AI fallback. Engines retain distinct cache identities.

Authenticated `POST /v1/translations/segments` requests use a fixed PostgreSQL
60-second quota window. Standard interactive requests reserve after cache
projection; realtime requests reserve only when the coordinator creates a new
provider batch. Only actual cache-miss text reserves user and global provider
characters. Cache hits, in-flight joins, and pending polls keep working without
a second charge. The defaults are 30 requests, 120,000 user miss
characters, and 500,000 global miss characters per minute and can be changed
with the three `APP_TRANSLATION_QUOTA_*` settings. PostgreSQL errors fail safely
with 503 and quota exhaustion returns a stable error code plus `Retry-After`.

The 403/429/503 quota envelopes belong only to the authenticated
`POST /v1/translations/segments` endpoint; its `Retry-After` header is part of
the OpenAPI contract. `POST /v1/translations/titles` remains a read/projection
endpoint and neither declares nor charges the translation quota. Regenerate
`mobile/lib/generated/api.ts` with `pnpm run generate:api-types` rather than
editing generated output.

Per-user UUID overrides are a break-glass operator operation and take effect
immediately without resetting the current window. Disabled users receive
403 `translation_disabled_for_user`, including realtime cache hits and joins.
Those authorization checks do not charge another request or character budget.
Quota windows are selected after all participating bucket locks are held and
advance together without moving backwards. There is no admin HTTP endpoint and no
global override. Use the production service connection through the CLI; never
pass a database credential as an argument. `--reason` is a controlled code,
not free-form prose: it must match
`[a-z][a-z0-9]*(?:[_-][a-z0-9]+){0,7}` (lowercase slug components, no spaces).
The optional `--reference` is a bounded incident/change identifier; it accepts
letters, digits, `.`, `_`, `:`, `@`, `+`, and `-`, and must never contain a DSN,
Bearer/JWT, API key, or control character.

```bash
uv run reader-admin translation-quota show <reader-user-uuid>
uv run reader-admin translation-quota set <reader-user-uuid> \
  --requests 60 --actual-miss-chars 240000 --actor operator@example.com \
  --reason temporary_support_allowance --reference INC-SUPPORT-123
uv run reader-admin translation-quota disable <reader-user-uuid> \
  --actor operator@example.com --reason abuse_mitigation --reference INC-ABUSE-456
uv run reader-admin translation-quota enable <reader-user-uuid> \
  --actor operator@example.com --reason mitigation_complete --reference INC-MITIGATION-789
uv run reader-admin translation-quota delete <reader-user-uuid> \
  --actor operator@example.com --reason remove_temporary_override --reference CHG-QUOTA-012
```
Every mutation appends a redacted, database-enforced append-only audit row.
Committed durable demand wakes the worker through PostgreSQL `LISTEN/NOTIFY`;
the 30-second safety check normally stays asleep, and a two-second fallback is
used only while the listener connection is unavailable. The worker retains
foreground and background lanes. Webpage/caption work bypasses those lanes and
uses the realtime coordinator with three-item urgent and five-item prefetch
batches. Provider-wide concurrency is bounded separately. See
[the translation execution paths](../docs/ARCHITECTURE.md#翻译的三条执行路径)
for the current structure, boundaries, and code entry points.

For predictable interactive latency, deploy the API, translation worker, and
PostgreSQL in the same region and keep both processes long-lived. Remote local
development pays the database network round trip several times across demand,
lease, artifact publication, and convergence. `APP_DATABASE_POOL_PRE_PING` is
enabled by default because hosted database intermediaries may drop idle pooled
connections; deployments that control the database lifecycle can disable it to
avoid the checkout round trip. SQLAlchemy pools are bounded to three
connections with no overflow by default and use a five-second checkout
timeout. Realtime preference and cache reads commit before quota, provider
capacity, DeepSeek, or subscriber waits; artifact persistence has its own short
session. This resource boundary, rather than matching database connections to
model concurrency, prevents pool starvation. When changing pool values, budget
the API process, worker storage pool, translation pool, and PostgreSQL
notification listeners together.

Ranking Snapshot refreshes are bounded separately from translation lanes. A
cycle defaults to four concurrent tasks and a 120-second total deadline; each
task opens its own short-lived `AsyncSession`, and cancellation clears only its
authoritative refresh lease. The worker records structured attempted,
succeeded, failed, skipped, and timed-out results in `last_metrics`; an
all-failed or all-timeout cycle fails its heartbeat, while a partial cycle
retains its partial metrics for `/worker-ready` diagnostics.

Realtime admission also bounds retained work: by default at most 16 runners and
500,000 source characters are retained through provider execution and artifact
persistence. Capacity waits create no new runner and consume no provider budget;
already accepted identities can still be joined.

Ingestion response limits are enforced on raw bytes and bounded gzip output.
Identity and gzip (including concatenated members) are supported; an upstream
that ignores the identity request and returns another content encoding receives
`unsupported_content_encoding` instead of unbounded automatic decompression.
Candidate projection failures roll back partial writes before recording a retry
under the current lease. An expired final-attempt lease becomes `failed` with
`lease_expired`, rather than remaining permanently `processing`.

## Run the development worker

Run this alongside the API while developing. Source, Candidate, ranking, and
cleanup Modules retain their schedules; translation execution drains on demand
and does not scan or backfill the whole content library:

```bash
uv run reader-worker
```

For one manual cycle, use `uv run reader-worker --once`.

Translation configuration is supervised independently from ingestion. A missing
translation credential disables and retries only the translation lanes; source,
candidate, ranking, and cleanup loops continue to run.

Each long-running loop writes a durable heartbeat. `GET /worker-ready` returns
503 until all source, Candidate, ranking, cleanup, and translation lanes have
completed successfully, and again when a lane fails or becomes stale. Heartbeat
rows from replaced worker instances are retained briefly for diagnosis and then
pruned automatically.

## Source ingestion

Scheduling and checkpoints live in `source_sync_states`; scanned items go
through the durable `ingestion_candidates` queue. Configure the backend-only
safety cap and YouTube key in `backend/.env`:

```env
APP_INGESTION_MAX_ITEMS_PER_SYNC=40
APP_INGESTION_DNS_TIMEOUT_SECONDS=3
APP_INGESTION_CONNECT_ATTEMPT_TIMEOUT_SECONDS=1.5
APP_YOUTUBE_DATA_API_KEY=...
APP_YOUTUBE_DAILY_QUOTA_SOFT_LIMIT=8000
APP_REDDIT_SUBSCRIPTION_LIMIT_PER_USER=20
APP_RANKING_PROVIDER_REFRESHES_PER_MINUTE=30
APP_REDDIT_RANKING_REFRESHES_PER_MINUTE=1
APP_RANKING_REFRESH_LEASE_SECONDS=240
APP_RANKING_WORKER_CONCURRENCY=4
APP_RANKING_WORKER_CYCLE_TIMEOUT_SECONDS=120
```

The mobile subscription API has no item-limit option. See
[the ingestion flow](../docs/ARCHITECTURE.md#订阅扫描与内容入库) and its linked code
for current source handling and recovery behavior.

Adding a website (`POST /v1/sources/web`) looks for its RSS/Atom feed during the
request and subscribes to that feed as an ordinary RSS source. Websites without
a usable feed are rejected with `feed_not_found`; Reader itself does not crawl
web pages. A separate crawling service is planned for such sites.

## Deployment and outbound-network safety

The migration history was squashed into a single baseline revision
(`migrations/versions/20261110_01_baseline`) on 2026-11-10. Databases created
before that revision cannot be upgraded and must be recreated. Apply the schema
before starting the API or worker:

```bash
uv run alembic upgrade head
```

Then start the worker and API. Keep public traffic
closed until the API's `GET /ready` and `GET /worker-ready` management probes
both return 200.

Run ordinary backend checks with implicit third-party dotenv loading disabled:

```bash
uv run ruff check src tests
PYTHON_DOTENV_DISABLED=1 uv run pytest -q -m 'not postgres'
```

Some third-party imports otherwise copy the developer's `.env` into the process
environment, invalidating tests that explicitly request missing settings.
Pydantic's explicitly configured settings files remain supported; this flag
does not disable the application's settings parser.

Run the destructive PostgreSQL migration gate against an automatically removed
Postgres 18 container before release:

```bash
./scripts/test-postgres-migrations.sh
```

The gate accepts only `127.0.0.1:<ephemeral-port>/reader_test`, and the script
creates a random ownership sentinel that pytest must verify before executing any
destructive SQL. The underlying pytest is skipped unless the explicit run flag,
DSN, and matching sentinel are present. Alembic also disables dotenv for that
run, so the gate cannot select the database configured in `backend/.env`.

The in-process authentication limiter uses bounded token buckets and charges
failed bearer-token verification, not successful traffic. It is a final guard,
not an Internet-edge control: configure a gateway rate limit and ensure the ASGI
server accepts forwarded client addresses only from trusted proxies.

User-supplied source fetches resolve and validate every address, then connect to
that exact public IP while preserving the original HTTP Host and TLS SNI. Treat
this as one layer, not the network boundary: production egress policy must also
deny loopback, RFC1918, link-local/metadata, carrier-grade NAT, and IPv6 ULA
destinations for untrusted HTTP traffic. Add narrow explicit exceptions only for
operator-configured internal services such as Scweet, and keep general Internet
egress limited to the ports required by source/provider adapters.
