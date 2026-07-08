#!/usr/bin/env python3
"""Diagnose server-mode X-Atlassian URL/PAT header authentication."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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


def _health_base_url(mcp_url: str) -> str:
    """Return the HTTP origin that serves /healthz for an MCP endpoint URL."""
    parts = urlsplit(mcp_url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


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
    except Exception as exc:  # noqa: BLE001 - diagnostic should report all failures
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

    base_url = _health_base_url(args.mcp_url)
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
            print(
                "[list_tools] jira visible:",
                any(t.startswith("jira_") for t in tool_names),
            )
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
    parser.add_argument(
        "--mcp-url", default=_env("MCP_URL", "http://localhost:9000/mcp")
    )
    parser.add_argument("--jira-url", default=_env("JIRA_BASE_URL"))
    parser.add_argument("--confluence-url", default=_env("CONFLUENCE_BASE_URL"))
    args = parser.parse_args()
    args.jira_pat = _env("JIRA_PERSONAL_TOKEN")
    args.confluence_pat = _env("CONFLUENCE_PERSONAL_TOKEN")
    return args


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
