from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_diagnostic_module():
    script_path = Path(__file__).with_name("diagnose-mcp-header-auth.py")
    spec = importlib.util.spec_from_file_location(
        "diagnose_mcp_header_auth", script_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_health_base_url_for_default_mcp_path():
    diagnostic = _load_diagnostic_module()

    assert (
        diagnostic._health_base_url("http://localhost:9000/mcp")
        == "http://localhost:9000"
    )


def test_health_base_url_for_custom_mcp_path():
    diagnostic = _load_diagnostic_module()

    assert (
        diagnostic._health_base_url("https://mcp.example.com/custom/path")
        == "https://mcp.example.com"
    )
