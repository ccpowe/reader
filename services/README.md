# Local services

## Scweet

Reader pins `Altimis/Scweet` v5.5.0 at commit
`0192d1a74a1421c61f75c35bd7917c18d90227fd`. The Docker build downloads that
archive and verifies its SHA-256 before installing the locked runtime
dependencies. `services/scweet/` remains an ignored local clone only for the
host-Python deployment. Scweet is an optional X collection integration.

It uses undocumented X web GraphQL endpoints and a dedicated account Cookie,
so it can break whenever X changes its web application. Keep requests bounded,
use a dedicated account rather than a personal one, and review X's terms and
applicable law before enabling it. The complete setup is in the root
[deployment guide](../docs/deployment.md#x-内容可选scweet).

### Clone and install again

```bash
git clone https://github.com/Altimis/Scweet.git services/scweet
cd services/scweet
git checkout 0192d1a74a1421c61f75c35bd7917c18d90227fd
uv venv .venv
uv pip install -e . -r ../scweet_service/requirements.in
```

### Credentials and safety

- Use a **dedicated X account**, never a personal account.
- In that account's authenticated browser session, copy the `auth_token`
  Cookie for `https://x.com`; Scweet can bootstrap `ct0` from it.
- Do not put cookies into `backend/.env`, Expo, Git, chat, or a GitHub secret.
  Store a local `cookies.json` inside `services/scweet/`; the parent repository
  ignores the local Scweet clone.

### Run as the reader's private service

The application includes `scweet_service/`: a narrow HTTP boundary that uses
this local fork. It has one authenticated profile-timeline endpoint and
serialises jobs, so one X account is never used concurrently.

### Recommended server deployment: systemd + host Python

The live Reader deployment runs Scweet as a host Python process, not as a
Docker container:

```text
systemd → reader-scweet.service → services/scweet/.venv/bin/uvicorn
        → services/scweet_service/app.py → local Scweet Python package
```

1. Clone Scweet into `services/scweet/` and install it with the command above.
   Its virtual environment must contain `uvicorn` and the editable `Scweet`
   package.
2. Copy [`systemd/reader-scweet.service.example`](systemd/reader-scweet.service.example)
   to `/etc/systemd/system/reader-scweet.service`. Replace `/opt/reader` with
   the absolute Reader checkout path, and create the unprivileged `reader`
   account if it does not already exist.
3. Create the state directory and private environment file:

```bash
sudo install -d -o reader -g reader -m 0700 /var/lib/reader-scweet
sudo install -d -m 0700 /etc/reader
sudo cp services/systemd/scweet.env.example /etc/reader/scweet.env
sudo chown root:reader /etc/reader/scweet.env
sudo chmod 0640 /etc/reader/scweet.env
# Edit /etc/reader/scweet.env: set the shared token and absolute Cookie path.
```

4. Enable and verify the service. `/health` reports process liveness; `/ready`
   additionally confirms that at least one locally configured account is
   currently eligible to collect:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now reader-scweet.service
systemctl status reader-scweet.service --no-pager
curl -fsS http://127.0.0.1:8090/health
curl -fsS http://127.0.0.1:8090/ready
```

5. Put the same `SCWEET_SERVICE_TOKEN` in the API process environment as
   `APP_SCWEET_SERVICE_TOKEN`, and use
   `APP_SCWEET_SERVICE_URL=http://127.0.0.1:8090`. Do not bind Uvicorn to a
   public address and do not expose port 8090 through a reverse proxy.

### Optional Docker deployment

Docker Compose builds from the pinned upstream archive; it does not require a
local Scweet clone. Keep `scweet/cookies.json` local, copy `.env.example` to
`.env` in this directory, set a long random `SCWEET_SERVICE_TOKEN`, then run:

```bash
docker compose -f docker-compose.scweet.yml -f docker-compose.scweet.dev.yml up --build -d
```

Put the same values into `backend/.env`:

```dotenv
APP_SCWEET_SERVICE_URL=http://127.0.0.1:8090
APP_SCWEET_SERVICE_TOKEN=the_same_long_random_token
```

The Cookie is mounted as a read-only runtime secret, the collector state stays
in the `scweet-state` volume, and neither enters the image. The service starts
only when the Cookie file is readable and imports at least one account with
complete local auth material. Temporary cooldowns and daily limits do not make
startup fail; `/ready` returns 503 until an account becomes eligible again, and
a request made while every account is unavailable returns a structured
retryable failure promptly.

The compose file intentionally does not publish port `8090`; attach the Reader
Worker to the `reader-internal` Docker network when it too runs in Docker and
set `APP_SCWEET_SERVICE_URL=http://scweet:8090`. Scweet also needs outbound DNS
and HTTPS access to X, so do not make its only network internal-only. The
`*.dev.yml` override is the only configuration that opens a port, and it is
bound to localhost. Do not expose this service publicly.

Set `APP_X_PROVIDER=scweet` explicitly (it is the default). Apify remains an
explicit compatibility path through `APP_X_PROVIDER=apify`; the backend never
switches providers automatically because their pagination checkpoints are not
interchangeable. If the selected collector is not configured, only X syncing
degrades; RSS, Web, Reddit, and YouTube continue normally. Authentication,
manifest, protected-account, and rate-limit failures are returned as structured
errors and use the backend retry policy.

The timeline endpoint returns an opaque continuation plus boundary tweet IDs.
The service replays and validates the previous boundary before advancing,
filters ads/recommendations, and keeps original tweets, quotes, replies,
retweets, and pinned events. Its cumulative collector safety window is 120
items per backend scan. When X returns a profile timeline that Scweet parses as
empty, the service performs one bounded 45-day `SearchTimeline` recovery for
the exact author handle. Recovered rows are author-validated, de-duplicated,
sorted by tweet ID, and capped to the requested limit. If neither timeline nor
search returns content, a public profile lookup distinguishes a valid dormant
account from an unverified empty result; the latter is reported as a structured
failure instead of a successful empty sync.
