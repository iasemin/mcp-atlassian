# Server Header PAT Authentication Design

## Goal

Enable HTTP server mode to work as a stateless multi-user gateway where each
request provides its own Atlassian Server/Data Center URL and Personal Access
Token (PAT) through explicit service headers.

The server must not require global Jira or Confluence user credentials for this
mode. Atlassian remains the source of authorization: every request runs under
the permissions of the PAT supplied on that request.

## Background

Local `stdio` mode is normally single-user. A user starts one MCP process with
`JIRA_URL` plus `JIRA_PERSONAL_TOKEN`, or the equivalent Confluence variables,
and all tool calls use that process-level identity.

HTTP server mode needs a different model. One MCP server can serve many users,
so credentials must be request-scoped. The existing code already has partial
support for header-based PAT authentication, but the intended behavior must be
made explicit, regression-tested, documented, and covered by a diagnostic stand.

## Auth Contract

For Jira, a request enables Jira tools only when both headers are present:

- `X-Atlassian-Jira-Url`
- `X-Atlassian-Jira-Personal-Token`

For Confluence, a request enables Confluence tools only when both headers are
present:

- `X-Atlassian-Confluence-Url`
- `X-Atlassian-Confluence-Personal-Token`

`Authorization: Token <PAT>` is not part of this Server/Data Center per-request
contract. This avoids mixing generic MCP transport authentication with
Atlassian service credentials.

## Expected Behavior

When a complete Jira header pair is present, the middleware stores the Jira URL
and PAT in request state, marks the request as PAT-authenticated for Atlassian,
and the Jira dependency builds a Jira fetcher from the request headers.

When a complete Confluence header pair is present, the same behavior applies to
the Confluence fetcher.

The tools list must expose Jira tools based on the Jira header pair and
Confluence tools based on the Confluence header pair, even when the server has
no global `JIRA_PERSONAL_TOKEN` or `CONFLUENCE_PERSONAL_TOKEN`.

If only one header from a pair is present, the service must not be treated as
available. Tool listing should not expose that service based on incomplete
headers. A direct tool call that reaches dependency resolution with incomplete
headers should fail with a clear configuration/authentication error.

If both header-based credentials and global environment credentials exist, the
header-based credentials win for the current request. The request must not fall
back to a global token after seeing a service header pair.

## Security Requirements

Header tokens must never be logged in clear text. Logs may record whether a
token is present and may use existing masking helpers when a masked value is
useful for diagnostics.

Every request-provided URL must pass existing SSRF validation before being used.
The fetcher's HTTP session must keep the existing redirect SSRF protection hook
for header-based requests.

Request-scoped fetchers may be cached only on `request.state`. They must not be
stored in process-global state or reused across different HTTP requests.

The MCP server does not implement Atlassian role logic. Jira and Confluence
enforce role-based permissions according to the supplied PAT.

## Code Design

`src/mcp_atlassian/servers/main.py`

- Keep `UserTokenMiddleware` as the single place that extracts HTTP headers.
- Ensure it stores complete Jira and Confluence service header pairs in
  `request.state.atlassian_service_headers`.
- Treat complete service header pairs as per-request PAT auth context without
  requiring an `Authorization` header.
- Leave unsupported or malformed `Authorization` handling unchanged.
- Preserve SSRF validation for `X-Atlassian-*-Url`.

`src/mcp_atlassian/servers/dependencies.py`

- Keep `_ServiceSpec` as the shared Jira/Confluence abstraction.
- Ensure `_get_fetcher()` takes the header-based branch when a complete service
  URL/token pair is present.
- Build service configs from request headers with `auth_type="pat"`.
- Attach the redirect SSRF hook for header-based requests.
- Validate credentials by calling the existing service-specific current-user
  validation method.
- Keep request-local fetcher caching on `request.state`.

`src/mcp_atlassian/servers/main.py` tool filtering

- Ensure `_list_tools_mcp()` uses complete per-request service header pairs to
  decide Jira/Confluence availability when no global config exists.
- Do not expose a service's tools for incomplete header pairs.

Docs

- Update HTTP transport/authentication documentation to present header-based
  Server/Data Center PAT auth as the supported multi-user server-mode contract.
- Make clear that local `stdio` mode remains process-env based.

## Test Strategy

Add or update unit tests for middleware behavior:

- Complete Jira headers populate `atlassian_service_headers` and set PAT
  request context without requiring `Authorization`.
- Complete Confluence headers do the same.
- Incomplete service headers do not make the service available.
- URL SSRF rejection still returns a 401-style auth error before the app runs.
- Header token values do not appear in logs.

Add or update unit tests for tool listing:

- Jira tools are listed with complete Jira headers and no global Jira auth.
- Confluence tools are listed with complete Confluence headers and no global
  Confluence auth.
- Each service is independently available based on its own complete header pair.
- Incomplete pairs do not expose tools.

Add or update dependency tests:

- Jira fetcher config uses request `X-Atlassian-Jira-Url` and
  `X-Atlassian-Jira-Personal-Token`.
- Confluence fetcher config uses request `X-Atlassian-Confluence-Url` and
  `X-Atlassian-Confluence-Personal-Token`.
- Header-based fetchers do not use global credentials for that request.
- Validation failures return clear `Invalid header-based ... token or
  configuration` errors.

## Diagnostic Stand

Extend the existing `tests/e2e/docker` stand instead of creating a separate
environment.

The stand should include:

- Jira Data Center and Confluence Data Center from the existing Docker Compose
  setup.
- Existing scripts for health checks, test data, and PAT generation.
- A local `mcp-atlassian` HTTP server running with `streamable-http`, preferably
  on port `9000`.
- A diagnostic client script that calls the MCP server with the four
  `X-Atlassian-*` headers.

The diagnostic script should:

- Check `/healthz`.
- Connect to `/mcp` using the streamable-http MCP client.
- Call `list_tools` and report whether Jira and Confluence tools are visible.
- Call low-risk read-only tools for Jira and Confluence.
- Print a concise report that distinguishes server reachability, missing
  headers, invalid URL, invalid token, and Atlassian permission failures.

The stand documentation should include a "Server-mode PAT headers" workflow
showing how to start Jira/Confluence, create PATs, start the MCP HTTP server,
and run the diagnostic script.

## Out of Scope

This change does not add OAuth proxy behavior, token refresh, token storage,
application-level role mapping, or a new authentication protocol for MCP clients.

This change does not remove the existing local `stdio` environment-variable
workflow.

## Acceptance Criteria

- Server HTTP mode works without global `JIRA_PERSONAL_TOKEN` or
  `CONFLUENCE_PERSONAL_TOKEN` when complete per-request headers are supplied.
- Each request is authorized by Atlassian under the supplied PAT.
- Jira and Confluence availability is decided independently per request.
- Incomplete headers fail closed.
- Tokens are not logged in clear text.
- Unit tests cover middleware, tool listing, and dependency behavior.
- The Docker-based diagnostic stand can verify and debug the header-based
  server-mode flow against Jira DC and Confluence DC.
