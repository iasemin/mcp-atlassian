# Jira DC + Confluence DC — Docker E2E Environment

Local Docker environment for running E2E tests against Jira Data Center and Confluence Data Center.

## Prerequisites

- **Docker Desktop** with at least **10 GB RAM** allocated (Settings > Resources > Memory)
- **curl** and **python3** available on your PATH
- Ports **8080** (Jira) and **8090** (Confluence) must be free

## Quick start

```bash
# 1. Copy env file and adjust if needed
cp .env.example .env

# 2. Start the services
docker compose up -d

# 3. Wait for both services to become healthy
bash healthcheck.sh

# 4. Complete the setup wizards in your browser (see below)

# 5. Create test data (project, space, issues, pages)
bash setup-test-data.sh

# 6. Create Personal Access Tokens for the test suite
bash create-pat.sh
```

## Server-mode PAT header diagnostic

Use this workflow to verify that one HTTP `mcp-atlassian` server can proxy
requests for different users by receiving Atlassian URL/PAT headers on each
MCP request.

Start Jira and Confluence first:

```bash
cd tests/e2e/docker
cp .env.example .env
docker compose up -d
bash healthcheck.sh
bash setup-test-data.sh
bash create-pat.sh
```

`create-pat.sh` prints `export` commands for `JIRA_PERSONAL_TOKEN` and
`CONFLUENCE_PERSONAL_TOKEN`; it does not write them to `.env`. Copy and run the
printed exports in your current shell, or save them in a local secrets file and
source that file before running the diagnostic.

Start the MCP HTTP server from the repository root in a separate terminal. Do
not set `JIRA_PERSONAL_TOKEN` or `CONFLUENCE_PERSONAL_TOKEN` for this diagnostic;
the script supplies those values through headers.

```bash
uv run mcp-atlassian --transport streamable-http --port 9000 -vv
```

If the MCP server runs in the same Docker network as Jira/Confluence and the
diagnostic passes Docker DNS names such as `http://jira:8080`, allowlist those
hostnames for SSRF validation:

```bash
MCP_ALLOWED_URL_DOMAINS=jira,confluence \
  uv run mcp-atlassian --transport streamable-http --port 9000 -vv
```

Run the diagnostic client:

```bash
cd tests/e2e/docker
source .env
# Paste/run the export commands printed by create-pat.sh here,
# or source your local secrets file that contains them.
uv run python diagnose-mcp-header-auth.py \
  --mcp-url http://localhost:9000/mcp \
  --jira-url "${JIRA_BASE_URL:-http://localhost:8080}" \
  --confluence-url "${CONFLUENCE_BASE_URL:-http://localhost:8090}"
```

For a fully containerized run, use URLs that are reachable from the MCP
container, not necessarily from the host:

```bash
uv run python diagnose-mcp-header-auth.py \
  --mcp-url http://localhost:9000/mcp \
  --jira-url http://jira:8080 \
  --confluence-url http://confluence:8090
```

The diagnostic reads `JIRA_PERSONAL_TOKEN` and
`CONFLUENCE_PERSONAL_TOKEN` from the environment. Avoid passing PATs as
command-line arguments because they can be exposed through process listings.

The report shows health check status, visible Jira/Confluence tools, and the
result of low-risk read-only calls. If a user's PAT has restricted permissions,
Jira or Confluence should return permission errors under that user's identity.

## Setup wizard (manual, one-time)

Both Jira and Confluence require completing a setup wizard on first launch.

### Jira (http://localhost:8080)

1. Select **I'll set it up myself**
2. Choose **My Own Database** — the DB is already configured via environment variables, so Jira should auto-detect it
3. Set application title and base URL (defaults are fine)
4. Enter a **license key** — paste the **Jira Software Data Center** timebomb key (10 user, 3 hours) from [Atlassian's testing licenses page](https://developer.atlassian.com/platform/marketplace/timebomb-licenses-for-testing-server-apps/) (under *Data Center host product licenses*). No my.atlassian.com account needed. See [License (timebomb)](#license-timebomb).
5. Create the admin account (default: `admin` / `admin123`)
6. Skip email configuration and language prompts

### Confluence (http://localhost:8090)

1. Select **Production Installation**
2. Enter a **license key** — paste the **Confluence Data Center** timebomb key (10 user, 3 hours) from [Atlassian's testing licenses page](https://developer.atlassian.com/platform/marketplace/timebomb-licenses-for-testing-server-apps/). **Tip:** set `CONFLUENCE_LICENSE_KEY` in `.env` — Confluence 7.9+ reads it from `ATL_LICENSE_KEY` at first-time setup, so this step can be skipped. See [License (timebomb)](#license-timebomb).
3. Choose **My own database** — again, auto-detected from environment
4. Skip the demo space
5. Configure user management (standalone, not connected to Jira)
6. Create the admin account (default: `admin` / `admin123`)

## License (timebomb)

These tests use Atlassian's published **Data Center timebomb licenses** (10 user, valid **3 hours** from when applied) instead of 30-day my.atlassian.com evals — they are free, public, and need no my.atlassian.com account. Get them from [Atlassian's testing licenses page](https://developer.atlassian.com/platform/marketplace/timebomb-licenses-for-testing-server-apps/) → *Data Center host product licenses* → **Jira Software Data Center** / **Confluence Data Center**.

The 3-hour limit is irrelevant for a normal run (the DC suite typically completes in under a minute); it only matters if an instance stays up for hours.

- **Confluence** — set `CONFLUENCE_LICENSE_KEY` in `.env` (see `.env.example`). Confluence 7.9+ reads it from `ATL_LICENSE_KEY` and applies it at **first-time setup** (skipping the wizard license step), writing it to `confluence.cfg.xml` on the persisted volume. It is **not** re-read on later restarts (`ATL_FORCE_CFG_UPDATE` defaults `false`), so a restart does **not** reset the 3-hour timer. To re-apply after expiry: a clean re-setup (`docker compose down -v`), or force a config refresh with `ATL_FORCE_CFG_UPDATE=true docker compose up -d confluence` (wired in `docker-compose.yml`). _(Verified on `confluence:9.2.21`: a fresh blank-DB start with this set skips the wizard license step. The no-restart-refresh detail follows Atlassian's container docs.)_
- **Jira** — has no license env var. Paste the timebomb key in the setup wizard. If it expires on a long-lived instance, re-paste at **Administration > System > License** (`/secure/admin/ViewLicense.jspa`, no restart needed) — still no my.atlassian.com.

> A 30-day my.atlassian.com eval also works for either product if you want a longer-lived local instance.
>
> **Fully unattended setup** (zero browser) is future work: seed a pre-configured database dump, or apply the Jira license post-boot via the private REST endpoint `POST /rest/plugins/applications/1.0/installed/jira-software/license`.

## Stopping and cleaning up

```bash
# Stop services (preserves data volumes)
docker compose down

# Stop and remove all data (full reset)
docker compose down -v
```

## Troubleshooting

| Problem | Solution |
| --- | --- |
| Service won't start | Check `docker compose logs jira` or `docker compose logs confluence` |
| Out of memory | Increase Docker Desktop RAM to 10 GB+ |
| Port conflict | Change the host port in `docker-compose.yml` (e.g., `9080:8080`) |
| DB connection error | Ensure the DB container is healthy: `docker compose ps` |
| Setup wizard reappears | Data volumes were removed — run `docker compose down` (without `-v`) to preserve them |
| License expired | See [License (timebomb)](#license-timebomb) — Jira: re-paste in admin; Confluence: `down -v` re-setup or `ATL_FORCE_CFG_UPDATE=true docker compose up -d confluence` (a plain restart does **not** refresh it) |
| MCP `/healthz` fails | Ensure `uv run mcp-atlassian --transport streamable-http --port 9000 -vv` is running from the repository root |
| Jira/Confluence tools are not visible | Confirm the diagnostic command passes both URL and PAT headers for that service |
| Invalid URL error | The MCP server rejected the supplied URL with SSRF validation; use a routable Jira/Confluence base URL, or set `MCP_ALLOWED_URL_DOMAINS` for trusted Docker DNS hostnames |
| Tool call returns 401/403 | The supplied PAT is invalid, expired, or lacks permission for that Jira/Confluence action |

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `JIRA_VERSION` | `10.3-jdk17` | Jira DC Docker image tag |
| `CONFLUENCE_VERSION` | `9.2-jdk17` | Confluence DC Docker image tag |
| `JIRA_DB_PASSWORD` | `jira_e2e_pass` | Jira PostgreSQL password |
| `CONFLUENCE_DB_PASSWORD` | `confluence_e2e_pass` | Confluence PostgreSQL password |
| `CONFLUENCE_LICENSE_KEY` | _(empty)_ | Confluence DC timebomb license, supplied at first-time setup via `ATL_LICENSE_KEY` (Confluence 7.9+; see [License](#license-timebomb)) |
| `JIRA_BASE_URL` | `http://localhost:8080` | Jira base URL (for scripts) |
| `CONFLUENCE_BASE_URL` | `http://localhost:8090` | Confluence base URL (for scripts) |
| `DC_ADMIN_CREDENTIALS` | `admin:admin123` | Admin credentials for REST API calls |
| `HEALTHCHECK_TIMEOUT` | `300` | Max wait time in seconds for healthcheck |
| `PAT_TOKEN_NAME` | `e2e-test-token` | Name for generated PAT tokens |
