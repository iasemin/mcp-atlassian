# Server Header PAT Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make HTTP server mode support per-request Jira/Confluence Server/Data Center PAT credentials supplied through `X-Atlassian-*` URL/token headers, plus provide a diagnostic stand for validating the flow.

**Architecture:** Keep `UserTokenMiddleware` as the HTTP header extraction boundary and keep `servers/dependencies.py` as the fetcher construction boundary. A complete service URL/token header pair creates request-scoped PAT auth, makes only that service's tools visible, and builds a request-local fetcher without global user credentials. Existing local `stdio` env-based auth remains unchanged.

**Tech Stack:** Python 3.10+, FastMCP, Starlette ASGI middleware, pytest/anyio, Docker Compose E2E stand, MCP streamable-http client.

---

## File Structure

- Modify `src/mcp_atlassian/servers/main.py`
  - Add small helpers for detecting complete Jira/Confluence header pairs.
  - Make complete `X-Atlassian-*` header pairs establish PAT request context before generic `Authorization` parsing.
  - Keep unsupported `Authorization` behavior unchanged when no complete service header pair exists.
- Modify `src/mcp_atlassian/servers/dependencies.py`
  - Add incomplete header-pair detection per service.
  - Fail closed with clear errors when a direct tool call reaches dependencies with only URL or only token for that service.
  - Preserve request-local fetcher caching, SSRF redirect hooks, and validation.
- Modify `tests/unit/servers/test_main_server.py`
  - Add middleware tests for complete service header pairs, `Authorization` precedence, incomplete pairs, and token redaction.
- Modify `tests/unit/servers/test_mcp_protocol.py`
  - Add tool-listing tests for header-only Jira/Confluence availability without global configs.
- Modify `tests/unit/servers/test_dependencies.py`
  - Add dependency tests for incomplete header pairs and header-over-global behavior.
- Modify `docs/http-transport.mdx`
  - Document Server/Data Center multi-user header-based PAT auth as the primary server-mode contract.
- Modify `docs/authentication.mdx`
  - Link the same contract from authentication docs.
- Create `tests/e2e/docker/diagnose-mcp-header-auth.py`
  - Provide a runnable diagnostic client for `/healthz`, `list_tools`, and low-risk read-only calls.
- Modify `tests/e2e/docker/README.md`
  - Add the server-mode PAT header diagnostic workflow.

## Task 1: Middleware Contract Tests

**Files:**
- Modify: `tests/unit/servers/test_main_server.py`

- [ ] **Step 1: Add failing tests for complete header-based PAT context**

Add these tests inside `class TestUserTokenMiddleware` after `test_basic_auth_extraction_success` or after the existing token extraction tests:

```python
    @pytest.mark.anyio
    async def test_complete_jira_service_headers_set_pat_context(
        self, middleware, mock_scope, mock_receive, mock_send
    ):
        """Complete Jira URL/PAT headers establish header-based PAT auth."""
        jira_token = b"jira-user-pat-secret"
        mock_scope["headers"] = [
            (b"x-atlassian-jira-url", b"https://jira.example.com"),
            (b"x-atlassian-jira-personal-token", jira_token),
        ]

        await middleware(mock_scope, mock_receive, mock_send)

        middleware.app.assert_called_once()
        downstream_scope = middleware.app.await_args.args[0]
        state = downstream_scope["state"]
        assert state["user_atlassian_auth_type"] == "pat"
        assert "user_atlassian_token" not in state
        assert state["atlassian_service_headers"] == {
            "X-Atlassian-Jira-Url": "https://jira.example.com",
            "X-Atlassian-Jira-Personal-Token": "jira-user-pat-secret",
        }

    @pytest.mark.anyio
    async def test_complete_confluence_service_headers_set_pat_context(
        self, middleware, mock_scope, mock_receive, mock_send
    ):
        """Complete Confluence URL/PAT headers establish header-based PAT auth."""
        mock_scope["headers"] = [
            (b"x-atlassian-confluence-url", b"https://confluence.example.com"),
            (
                b"x-atlassian-confluence-personal-token",
                b"confluence-user-pat-secret",
            ),
        ]

        await middleware(mock_scope, mock_receive, mock_send)

        middleware.app.assert_called_once()
        downstream_scope = middleware.app.await_args.args[0]
        state = downstream_scope["state"]
        assert state["user_atlassian_auth_type"] == "pat"
        assert "user_atlassian_token" not in state
        assert state["atlassian_service_headers"] == {
            "X-Atlassian-Confluence-Url": "https://confluence.example.com",
            "X-Atlassian-Confluence-Personal-Token": "confluence-user-pat-secret",
        }
```

- [ ] **Step 2: Add failing tests for header pair precedence and incomplete pairs**

Add these tests in the same class:

```python
    @pytest.mark.anyio
    async def test_service_headers_take_precedence_over_authorization_for_atlassian_auth(
        self, middleware, mock_scope, mock_receive, mock_send
    ):
        """A gateway Authorization header must not override X-Atlassian PAT auth."""
        mock_scope["headers"] = [
            (b"authorization", b"Bearer gateway-or-mcp-token"),
            (b"x-atlassian-jira-url", b"https://jira.example.com"),
            (b"x-atlassian-jira-personal-token", b"jira-user-pat-secret"),
        ]

        await middleware(mock_scope, mock_receive, mock_send)

        middleware.app.assert_called_once()
        downstream_scope = middleware.app.await_args.args[0]
        state = downstream_scope["state"]
        assert state["user_atlassian_auth_type"] == "pat"
        assert "user_atlassian_token" not in state
        assert state["atlassian_service_headers"][
            "X-Atlassian-Jira-Personal-Token"
        ] == "jira-user-pat-secret"

    @pytest.mark.anyio
    async def test_incomplete_service_headers_do_not_set_pat_context(
        self, middleware, mock_scope, mock_receive, mock_send
    ):
        """A lone service URL or PAT header is captured but does not authenticate."""
        mock_scope["headers"] = [
            (b"x-atlassian-jira-url", b"https://jira.example.com"),
        ]

        await middleware(mock_scope, mock_receive, mock_send)

        middleware.app.assert_called_once()
        downstream_scope = middleware.app.await_args.args[0]
        state = downstream_scope["state"]
        assert "user_atlassian_auth_type" not in state
        assert "user_atlassian_token" not in state
        assert state["atlassian_service_headers"] == {
            "X-Atlassian-Jira-Url": "https://jira.example.com",
        }
```

- [ ] **Step 3: Add failing log redaction test**

Add this test in the same class:

```python
    @pytest.mark.anyio
    async def test_service_header_pat_value_is_not_logged(
        self, middleware, mock_scope, mock_receive, mock_send, caplog
    ):
        """Header PAT values must not appear in middleware logs."""
        secret = "jira-user-pat-secret"
        mock_scope["headers"] = [
            (b"x-atlassian-jira-url", b"https://jira.example.com"),
            (b"x-atlassian-jira-personal-token", secret.encode("utf-8")),
        ]

        with caplog.at_level(logging.DEBUG, logger="mcp-atlassian.server.main"):
            await middleware(mock_scope, mock_receive, mock_send)

        assert secret not in caplog.text
        assert "X-Atlassian-Jira-Personal-Token" in caplog.text
```

- [ ] **Step 4: Run middleware tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/servers/test_main_server.py::TestUserTokenMiddleware -xvs
```

Expected: at least `test_service_headers_take_precedence_over_authorization_for_atlassian_auth` fails because current middleware parses `Authorization` after service headers and sets `user_atlassian_token`.

- [ ] **Step 5: Commit failing tests**

```bash
git add tests/unit/servers/test_main_server.py
git commit -m "test(server): cover header-based pat middleware contract"
```

## Task 2: Middleware Header-Based PAT Implementation

**Files:**
- Modify: `src/mcp_atlassian/servers/main.py:484-595`
- Test: `tests/unit/servers/test_main_server.py`

- [ ] **Step 1: Add service header helper functions near `token_validation_cache`**

Insert this code above `class UserTokenMiddleware`:

```python
def _has_complete_service_header_pair(
    service_headers: dict[str, str],
    url_header: str,
    token_header: str,
) -> bool:
    """Return True when a service has both URL and PAT headers."""
    return bool(service_headers.get(url_header) and service_headers.get(token_header))


def _has_any_complete_service_header_pair(service_headers: dict[str, str]) -> bool:
    """Return True when Jira or Confluence has a complete URL/PAT header pair."""
    return _has_complete_service_header_pair(
        service_headers,
        "X-Atlassian-Jira-Url",
        "X-Atlassian-Jira-Personal-Token",
    ) or _has_complete_service_header_pair(
        service_headers,
        "X-Atlassian-Confluence-Url",
        "X-Atlassian-Confluence-Personal-Token",
    )
```

- [ ] **Step 2: Update `_process_authentication_headers` to prioritize service headers**

Replace the block from:

```python
            # Process Authorization header
            if auth_header_str:
                self._parse_auth_header(auth_header_str, scope)
            else:
                logger.debug("UserTokenMiddleware: No Authorization header provided")
                # If service headers are present without Authorization header, set PAT auth type
                if service_headers and (
                    (jira_token_str and jira_url_str)
                    or (confluence_token_str and confluence_url_str)
                ):
                    scope["state"]["user_atlassian_auth_type"] = "pat"
                    scope["state"]["user_atlassian_email"] = None
                    logger.debug(
                        "UserTokenMiddleware: Header-based authentication detected. Setting PAT auth type."
                    )
```

with:

```python
            complete_service_headers = _has_any_complete_service_header_pair(
                service_headers
            )
            if complete_service_headers:
                scope["state"]["user_atlassian_auth_type"] = "pat"
                scope["state"]["user_atlassian_email"] = None
                logger.debug(
                    "UserTokenMiddleware: Header-based authentication detected. "
                    "Setting PAT auth type."
                )
                return

            # Process Authorization header only when no complete Atlassian
            # service header pair is present. This keeps generic gateway/MCP
            # Authorization from overriding per-request Atlassian PAT headers.
            if auth_header_str:
                self._parse_auth_header(auth_header_str, scope)
            else:
                logger.debug("UserTokenMiddleware: No Authorization header provided")
```

- [ ] **Step 3: Run middleware tests**

Run:

```bash
uv run pytest tests/unit/servers/test_main_server.py::TestUserTokenMiddleware -xvs
```

Expected: PASS.

- [ ] **Step 4: Run SSRF middleware tests**

Run:

```bash
uv run pytest tests/unit/servers/test_main_server.py::TestUserTokenMiddlewareSsrfValidation -xvs
```

Expected: PASS. The early SSRF rejection path still runs before service header PAT context is set.

- [ ] **Step 5: Commit middleware implementation**

```bash
git add src/mcp_atlassian/servers/main.py tests/unit/servers/test_main_server.py
git commit -m "fix(server): prioritize header-based atlassian pat auth"
```

## Task 3: Tool Listing Tests for Header-Only Availability

**Files:**
- Modify: `tests/unit/servers/test_mcp_protocol.py`

- [ ] **Step 1: Add helper functions in `TestMCPProtocolIntegration`**

Add these methods inside `class TestMCPProtocolIntegration` before the first test:

```python
    def _make_tool(self, name: str, tags: set[str]) -> FastMCPTool:
        tool = MagicMock(spec=FastMCPTool)
        tool.tags = tags
        tool.to_mcp_tool.return_value = MCPTool(
            name=name,
            description=f"Tool {name}",
            inputSchema={"type": "object", "properties": {}},
        )
        return tool

    def _attach_request_context(
        self,
        atlassian_mcp_server: AtlassianMCP,
        service_headers: dict[str, str],
    ) -> None:
        app_context = MainAppContext(
            full_jira_config=None,
            full_confluence_config=None,
            read_only=False,
            enabled_tools=None,
            enabled_toolsets=None,
        )
        request = MagicMock()
        request.state.atlassian_service_headers = service_headers
        request_context = MagicMock()
        request_context.lifespan_context = {"app_lifespan_context": app_context}
        request_context.request = request
        atlassian_mcp_server._mcp_server = MagicMock()
        atlassian_mcp_server._mcp_server.request_context = request_context
```

- [ ] **Step 2: Add failing Jira-only tool listing test**

Add this test in the same class:

```python
    async def test_tool_listing_uses_jira_header_pair_without_global_config(
        self, atlassian_mcp_server
    ):
        """Jira tools are visible from complete per-request Jira URL/PAT headers."""
        self._attach_request_context(
            atlassian_mcp_server,
            {
                "X-Atlassian-Jira-Url": "https://jira.example.com",
                "X-Atlassian-Jira-Personal-Token": "jira-user-pat-secret",
            },
        )

        async def mock_get_tools():
            return {
                "jira_search": self._make_tool("jira_search", {"jira", "read"}),
                "confluence_search": self._make_tool(
                    "confluence_search", {"confluence", "read"}
                ),
            }

        atlassian_mcp_server.get_tools = mock_get_tools

        tools = await atlassian_mcp_server._list_tools_mcp()

        assert [tool.name for tool in tools] == ["jira_search"]
```

- [ ] **Step 3: Add failing Confluence-only and incomplete-header tests**

Add these tests in the same class:

```python
    async def test_tool_listing_uses_confluence_header_pair_without_global_config(
        self, atlassian_mcp_server
    ):
        """Confluence tools are visible from complete per-request URL/PAT headers."""
        self._attach_request_context(
            atlassian_mcp_server,
            {
                "X-Atlassian-Confluence-Url": "https://confluence.example.com",
                "X-Atlassian-Confluence-Personal-Token": "confluence-user-pat-secret",
            },
        )

        async def mock_get_tools():
            return {
                "jira_search": self._make_tool("jira_search", {"jira", "read"}),
                "confluence_search": self._make_tool(
                    "confluence_search", {"confluence", "read"}
                ),
            }

        atlassian_mcp_server.get_tools = mock_get_tools

        tools = await atlassian_mcp_server._list_tools_mcp()

        assert [tool.name for tool in tools] == ["confluence_search"]

    async def test_tool_listing_does_not_enable_service_from_incomplete_headers(
        self, atlassian_mcp_server
    ):
        """A lone service URL header must not expose that service's tools."""
        self._attach_request_context(
            atlassian_mcp_server,
            {"X-Atlassian-Jira-Url": "https://jira.example.com"},
        )

        async def mock_get_tools():
            return {
                "jira_search": self._make_tool("jira_search", {"jira", "read"}),
                "confluence_search": self._make_tool(
                    "confluence_search", {"confluence", "read"}
                ),
            }

        atlassian_mcp_server.get_tools = mock_get_tools

        tools = await atlassian_mcp_server._list_tools_mcp()

        assert tools == []
```

- [ ] **Step 4: Run the new protocol tests**

Run:

```bash
uv run pytest tests/unit/servers/test_mcp_protocol.py::TestMCPProtocolIntegration -k "header_pair or incomplete_headers" -xvs
```

Expected: PASS. `MainAppContext` accepts `enabled_toolsets=None`, so keep that
argument in `_attach_request_context()`.

- [ ] **Step 5: Commit tool listing tests**

```bash
git add tests/unit/servers/test_mcp_protocol.py
git commit -m "test(server): cover header-based service tool discovery"
```

## Task 4: Dependency Fail-Closed Tests

**Files:**
- Modify: `tests/unit/servers/test_dependencies.py`

- [ ] **Step 1: Import helper under test**

Extend the import from `mcp_atlassian.servers.dependencies` to include `_has_incomplete_service_header_pair` after it is implemented in Task 5. The import should become:

```python
from mcp_atlassian.servers.dependencies import (
    _create_user_config_for_fetcher,
    _has_incomplete_service_header_pair,
    _resolve_bearer_auth_type,
    get_confluence_fetcher,
    get_jira_fetcher,
)
```

This import will fail until Task 5 adds the helper.

- [ ] **Step 2: Add failing helper tests near `TestResolveBearerAuthType`**

Add this class before `class TestSsrfProtection`:

```python
class TestHeaderPairCompleteness:
    """Tests for request-scoped service header completeness detection."""

    def test_jira_incomplete_when_only_url_present(self):
        headers = {"X-Atlassian-Jira-Url": "https://jira.example.com"}
        assert _has_incomplete_service_header_pair(
            headers,
            "X-Atlassian-Jira-Url",
            "X-Atlassian-Jira-Personal-Token",
        )

    def test_jira_incomplete_when_only_token_present(self):
        headers = {"X-Atlassian-Jira-Personal-Token": "jira-user-pat-secret"}
        assert _has_incomplete_service_header_pair(
            headers,
            "X-Atlassian-Jira-Url",
            "X-Atlassian-Jira-Personal-Token",
        )

    def test_jira_complete_pair_is_not_incomplete(self):
        headers = {
            "X-Atlassian-Jira-Url": "https://jira.example.com",
            "X-Atlassian-Jira-Personal-Token": "jira-user-pat-secret",
        }
        assert not _has_incomplete_service_header_pair(
            headers,
            "X-Atlassian-Jira-Url",
            "X-Atlassian-Jira-Personal-Token",
        )
```

- [ ] **Step 3: Add failing direct dependency tests**

Add this test to `class TestGetJiraFetcher`:

```python
    @patch("mcp_atlassian.servers.dependencies.get_http_request")
    async def test_incomplete_header_pair_fails_closed_before_global_fallback(
        self,
        mock_get_http_request,
        mock_context,
        mock_request,
        config_factory,
    ):
        """A partial Jira service header pair must not fall back to global auth."""

        class MockState:
            def __init__(self):
                self.jira_fetcher = None
                self.user_atlassian_auth_type = None
                self.user_atlassian_email = None
                self.atlassian_service_headers = {
                    "X-Atlassian-Jira-Url": "https://jira.example.com"
                }

            def __getattr__(self, name):
                if name == "user_atlassian_token":
                    raise AttributeError(name)
                return None

        mock_request.state = MockState()
        mock_get_http_request.return_value = mock_request
        app_context = config_factory.create_app_context(
            jira_config=config_factory.create_jira_config(auth_type="pat")
        )
        _setup_mock_context(mock_context, app_context)

        with pytest.raises(
            ValueError,
            match=(
                "Incomplete header-based Jira authentication: provide both "
                "X-Atlassian-Jira-Url and X-Atlassian-Jira-Personal-Token"
            ),
        ):
            await get_jira_fetcher(mock_context)
```

Add this test to `class TestGetConfluenceFetcher`:

```python
    @patch("mcp_atlassian.servers.dependencies.get_http_request")
    async def test_incomplete_header_pair_fails_closed_before_global_fallback(
        self,
        mock_get_http_request,
        mock_context,
        mock_request,
        config_factory,
    ):
        """A partial Confluence service header pair must not fall back globally."""

        class MockState:
            def __init__(self):
                self.confluence_fetcher = None
                self.user_atlassian_auth_type = None
                self.user_atlassian_email = None
                self.atlassian_service_headers = {
                    "X-Atlassian-Confluence-Personal-Token": (
                        "confluence-user-pat-secret"
                    )
                }

            def __getattr__(self, name):
                if name == "user_atlassian_token":
                    raise AttributeError(name)
                return None

        mock_request.state = MockState()
        mock_get_http_request.return_value = mock_request
        app_context = config_factory.create_app_context(
            confluence_config=config_factory.create_confluence_config(auth_type="pat")
        )
        _setup_mock_context(mock_context, app_context)

        with pytest.raises(
            ValueError,
            match=(
                "Incomplete header-based Confluence authentication: provide both "
                "X-Atlassian-Confluence-Url and "
                "X-Atlassian-Confluence-Personal-Token"
            ),
        ):
            await get_confluence_fetcher(mock_context)
```

- [ ] **Step 4: Run dependency tests and confirm failure**

Run:

```bash
uv run pytest tests/unit/servers/test_dependencies.py -k "HeaderPairCompleteness or incomplete_header_pair" -xvs
```

Expected: FAIL because `_has_incomplete_service_header_pair` does not exist and `_get_fetcher()` currently falls through to global fallback for incomplete service headers.

- [ ] **Step 5: Commit failing dependency tests**

```bash
git add tests/unit/servers/test_dependencies.py
git commit -m "test(server): cover incomplete header auth failure"
```

## Task 5: Dependency Fail-Closed Implementation

**Files:**
- Modify: `src/mcp_atlassian/servers/dependencies.py:160-690`
- Test: `tests/unit/servers/test_dependencies.py`

- [ ] **Step 1: Add incomplete header helper near shared helpers**

Add this function below `_get_global_config`:

```python
def _has_incomplete_service_header_pair(
    service_headers: dict[str, str],
    url_header: str,
    token_header: str,
) -> bool:
    """Return True when exactly one service URL/PAT header is present."""
    has_url = bool(service_headers.get(url_header))
    has_token = bool(service_headers.get(token_header))
    return has_url != has_token
```

- [ ] **Step 2: Fail closed in `_get_fetcher()` before global fallback**

Insert this block immediately after:

```python
        url_header_val = service_headers.get(spec.url_header)
        token_header_val = service_headers.get(spec.token_header)
```

```python
        if _has_incomplete_service_header_pair(
            service_headers, spec.url_header, spec.token_header
        ):
            raise ValueError(
                f"Incomplete header-based {spec.name} authentication: provide both "
                f"{spec.url_header} and {spec.token_header}."
            )
```

- [ ] **Step 3: Remove the trailing period mismatch if tests require exact match**

If the regular expression in Task 4 fails because of the final period, update the expected regex strings in the tests to include `\\.` at the end. Keep the implementation message with the period because it reads better in logs and API errors:

```python
            match=(
                "Incomplete header-based Jira authentication: provide both "
                "X-Atlassian-Jira-Url and X-Atlassian-Jira-Personal-Token\\."
            ),
```

- [ ] **Step 4: Run focused dependency tests**

Run:

```bash
uv run pytest tests/unit/servers/test_dependencies.py -k "HeaderPairCompleteness or incomplete_header_pair or header_based_jira_fetcher_creation or header_based_confluence_fetcher_creation" -xvs
```

Expected: PASS.

- [ ] **Step 5: Run broader server dependency tests**

Run:

```bash
uv run pytest tests/unit/servers/test_dependencies.py -xvs
```

Expected: PASS.

- [ ] **Step 6: Commit dependency implementation**

```bash
git add src/mcp_atlassian/servers/dependencies.py tests/unit/servers/test_dependencies.py
git commit -m "fix(server): fail closed on incomplete service auth headers"
```

## Task 6: HTTP Authentication Documentation

**Files:**
- Modify: `docs/http-transport.mdx`
- Modify: `docs/authentication.mdx`

- [ ] **Step 1: Update `docs/http-transport.mdx` Server/DC tab**

Replace the current Server/DC PAT tab under "Authentication Methods" with:

````mdx
  <Tab title="Server/DC (PAT headers)">
    ```json
    {
      "mcpServers": {
        "mcp-atlassian-service": {
          "url": "http://localhost:9000/mcp",
          "headers": {
            "X-Atlassian-Jira-Url": "https://jira.company.com",
            "X-Atlassian-Jira-Personal-Token": "<USER_JIRA_PAT>",
            "X-Atlassian-Confluence-Url": "https://confluence.company.com",
            "X-Atlassian-Confluence-Personal-Token": "<USER_CONFLUENCE_PAT>"
          }
        }
      }
    }
    ```

    Send only the Jira headers when the user should access Jira only, only the
    Confluence headers when the user should access Confluence only, or all four
    headers when both services should be available. The MCP server does not store
    these PATs; Jira and Confluence enforce permissions for the supplied user.
  </Tab>
````

- [ ] **Step 2: Add a Server/DC setup note below "Server Setup for Multi-User"**

Add this paragraph before the OAuth setup step:

````mdx
For Server/Data Center PAT multi-user mode, the HTTP server can be started
without `JIRA_PERSONAL_TOKEN` or `CONFLUENCE_PERSONAL_TOKEN`. Each request must
provide a complete service header pair:

```bash
uvx mcp-atlassian --transport streamable-http --port 9000 -vv
```

The server treats `X-Atlassian-Jira-Url` + `X-Atlassian-Jira-Personal-Token`
and `X-Atlassian-Confluence-Url` +
`X-Atlassian-Confluence-Personal-Token` as request-scoped credentials.
````

- [ ] **Step 3: Update `docs/authentication.mdx` multi-user section**

Replace the bullet list under "Users provide authentication via HTTP headers" with:

```mdx
2. Users provide authentication via HTTP headers:
   - Cloud OAuth: `Authorization: Bearer <user_oauth_token>` plus `X-Atlassian-Cloud-Id: <user_cloud_id>`
   - Server/Data Center Jira PAT: `X-Atlassian-Jira-Url` plus `X-Atlassian-Jira-Personal-Token`
   - Server/Data Center Confluence PAT: `X-Atlassian-Confluence-Url` plus `X-Atlassian-Confluence-Personal-Token`
```

- [ ] **Step 4: Run docs grep checks**

Run:

```bash
rg -n "Authorization\": \"Token|Authorization: Token|X-Atlassian-Jira-Personal-Token|X-Atlassian-Confluence-Personal-Token" docs/http-transport.mdx docs/authentication.mdx
```

Expected: no remaining `Authorization: Token` recommendation in these docs for server-mode Server/DC PAT; new `X-Atlassian-*` headers are present.

- [ ] **Step 5: Commit docs**

```bash
git add docs/http-transport.mdx docs/authentication.mdx
git commit -m "docs(server): document header-based pat auth"
```

## Task 7: Diagnostic Client Script

**Files:**
- Create: `tests/e2e/docker/diagnose-mcp-header-auth.py`

- [ ] **Step 1: Create the diagnostic script**

Create `tests/e2e/docker/diagnose-mcp-header-auth.py` with this content:

```python
#!/usr/bin/env python3
"""Diagnose server-mode X-Atlassian URL/PAT header authentication."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _build_headers(args: argparse.Namespace) -> dict[str, str]:
    headers: dict[str, str] = {}
    if args.jira_url and args.jira_pat:
        headers["X-Atlassian-Jira-Url"] = args.jira_url
        headers["X-Atlassian-Jira-Personal-Token"] = args.jira_pat
    if args.confluence_url and args.confluence_pat:
        headers["X-Atlassian-Confluence-Url"] = args.confluence_url
        headers["X-Atlassian-Confluence-Personal-Token"] = args.confluence_pat
    return headers


def _mask(value: str | None) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


async def _check_health(base_url: str) -> None:
    health_url = base_url.rstrip("/") + "/healthz"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(health_url)
    print(f"[healthz] {response.status_code} {response.text}")
    response.raise_for_status()


async def _call_tool_safe(
    session: ClientSession, tool_name: str, arguments: dict[str, Any]
) -> None:
    try:
        result = await session.call_tool(tool_name, arguments)
    except Exception as exc:
        print(f"[tool:{tool_name}] ERROR {type(exc).__name__}: {exc}")
        return
    print(f"[tool:{tool_name}] OK {result}")


async def _run(args: argparse.Namespace) -> int:
    headers = _build_headers(args)
    print("[config] MCP URL:", args.mcp_url)
    print("[config] Jira URL:", args.jira_url or "<missing>")
    print("[config] Jira PAT:", _mask(args.jira_pat))
    print("[config] Confluence URL:", args.confluence_url or "<missing>")
    print("[config] Confluence PAT:", _mask(args.confluence_pat))
    print("[config] Header names:", sorted(headers))

    if not headers:
        print(
            "[error] No complete Jira or Confluence URL/PAT header pair was supplied.",
            file=sys.stderr,
        )
        return 2

    base_url = args.mcp_url.rsplit("/mcp", 1)[0]
    await _check_health(base_url)

    async with streamablehttp_client(args.mcp_url, headers=headers) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = sorted(tool.name for tool in tools.tools)
            print(f"[list_tools] count={len(tool_names)}")
            print("[list_tools] jira visible:", any(t.startswith("jira_") for t in tool_names))
            print(
                "[list_tools] confluence visible:",
                any(t.startswith("confluence_") for t in tool_names),
            )

            if args.jira_url and args.jira_pat:
                if "jira_search" in tool_names:
                    await _call_tool_safe(
                        session,
                        "jira_search",
                        {"jql": "order by created DESC", "limit": 1},
                    )
                else:
                    print("[tool:jira_search] SKIP tool is not visible")

            if args.confluence_url and args.confluence_pat:
                if "confluence_search" in tool_names:
                    await _call_tool_safe(
                        session,
                        "confluence_search",
                        {"query": "type=page", "limit": 1},
                    )
                else:
                    print("[tool:confluence_search] SKIP tool is not visible")

    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose mcp-atlassian server-mode PAT header auth."
    )
    parser.add_argument("--mcp-url", default=_env("MCP_URL", "http://localhost:9000/mcp"))
    parser.add_argument("--jira-url", default=_env("JIRA_BASE_URL"))
    parser.add_argument("--jira-pat", default=_env("JIRA_PERSONAL_TOKEN"))
    parser.add_argument("--confluence-url", default=_env("CONFLUENCE_BASE_URL"))
    parser.add_argument(
        "--confluence-pat", default=_env("CONFLUENCE_PERSONAL_TOKEN")
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Make the script executable**

Run:

```bash
chmod +x tests/e2e/docker/diagnose-mcp-header-auth.py
```

- [ ] **Step 3: Run import/syntax check**

Run:

```bash
uv run python -m py_compile tests/e2e/docker/diagnose-mcp-header-auth.py
```

Expected: PASS with no output.

- [ ] **Step 4: Commit diagnostic script**

```bash
git add tests/e2e/docker/diagnose-mcp-header-auth.py
git commit -m "test(e2e): add header pat auth diagnostic client"
```

## Task 8: Diagnostic Stand Documentation

**Files:**
- Modify: `tests/e2e/docker/README.md`

- [ ] **Step 1: Add a server-mode diagnostic section after Quick start**

Add this section after the existing quick start block:

````markdown
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

Start the MCP HTTP server from the repository root in a separate terminal. Do
not set `JIRA_PERSONAL_TOKEN` or `CONFLUENCE_PERSONAL_TOKEN` for this diagnostic;
the script supplies those values through headers.

```bash
uv run mcp-atlassian --transport streamable-http --port 9000 -vv
```

Run the diagnostic client:

```bash
cd tests/e2e/docker
source .env
uv run python diagnose-mcp-header-auth.py \
  --mcp-url http://localhost:9000/mcp \
  --jira-url "${JIRA_BASE_URL:-http://localhost:8080}" \
  --jira-pat "$JIRA_PERSONAL_TOKEN" \
  --confluence-url "${CONFLUENCE_BASE_URL:-http://localhost:8090}" \
  --confluence-pat "$CONFLUENCE_PERSONAL_TOKEN"
```

The report shows health check status, visible Jira/Confluence tools, and the
result of low-risk read-only calls. If a user's PAT has restricted permissions,
Jira or Confluence should return permission errors under that user's identity.
````

- [ ] **Step 2: Add troubleshooting rows**

Append these rows to the troubleshooting table:

```markdown
| MCP `/healthz` fails | Ensure `uv run mcp-atlassian --transport streamable-http --port 9000 -vv` is running from the repository root |
| Jira/Confluence tools are not visible | Confirm the diagnostic command passes both URL and PAT headers for that service |
| Invalid URL error | The MCP server rejected the supplied URL with SSRF validation; use a routable Jira/Confluence base URL |
| Tool call returns 401/403 | The supplied PAT is invalid, expired, or lacks permission for that Jira/Confluence action |
```

- [ ] **Step 3: Commit stand docs**

```bash
git add tests/e2e/docker/README.md
git commit -m "docs(e2e): describe server header pat diagnostic stand"
```

## Task 9: Final Verification

**Files:**
- Verify all changed source, tests, docs, and diagnostic script.

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
uv run pytest \
  tests/unit/servers/test_main_server.py::TestUserTokenMiddleware \
  tests/unit/servers/test_main_server.py::TestUserTokenMiddlewareSsrfValidation \
  tests/unit/servers/test_mcp_protocol.py::TestMCPProtocolIntegration \
  tests/unit/servers/test_dependencies.py \
  -xvs
```

Expected: PASS.

- [ ] **Step 2: Run unit server suite**

Run:

```bash
uv run pytest tests/unit/servers/ -xvs
```

Expected: PASS.

- [ ] **Step 3: Run lint/format checks if available**

Run:

```bash
uv run ruff check src/mcp_atlassian/servers tests/unit/servers tests/e2e/docker/diagnose-mcp-header-auth.py
```

Expected: PASS.

Run:

```bash
uv run ruff format --check src/mcp_atlassian/servers tests/unit/servers tests/e2e/docker/diagnose-mcp-header-auth.py
```

Expected: PASS.

- [ ] **Step 4: Run diagnostic stand when Docker Atlassian services are available**

Run from repository root:

```bash
uv run mcp-atlassian --transport streamable-http --port 9000 -vv
```

In another terminal, run:

```bash
cd tests/e2e/docker
source .env
uv run python diagnose-mcp-header-auth.py \
  --mcp-url http://localhost:9000/mcp \
  --jira-url "${JIRA_BASE_URL:-http://localhost:8080}" \
  --jira-pat "$JIRA_PERSONAL_TOKEN" \
  --confluence-url "${CONFLUENCE_BASE_URL:-http://localhost:8090}" \
  --confluence-pat "$CONFLUENCE_PERSONAL_TOKEN"
```

Expected output contains these lines:

```text
[healthz] 200 {"status":"ok"}
[list_tools] jira visible: True
[list_tools] confluence visible: True
[tool:jira_search] OK
[tool:confluence_search] OK
```

If Docker services are not available in the execution environment, record that
manual stand verification was not run and include the reason in the final
handoff.

- [ ] **Step 5: Review git diff**

Run:

```bash
git status --short
git log --oneline --max-count=8
```

Expected: working tree is clean after commits; recent commits correspond to the
tasks above.
