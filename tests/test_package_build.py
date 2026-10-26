"""Tests for package build, wheel/sdist safety audit, and sensitive data exclusion."""

import subprocess
import tarfile
import zipfile
from pathlib import Path
import pytest


def test_package_build_and_safety_audit():
    """Verify that uv build succeeds and produces clean distributions without sensitive data."""
    root_dir = Path(__file__).resolve().parent.parent
    dist_dir = root_dir / "dist"

    # 1. Run uv build
    res = subprocess.run(
        ["uv", "build"],
        cwd=root_dir,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"uv build failed: {res.stderr}"

    wheels = list(dist_dir.glob("*.whl"))
    sdists = list(dist_dir.glob("*.tar.gz"))

    assert len(wheels) > 0, "No wheel built in dist/"
    assert len(sdists) > 0, "No source distribution built in dist/"

    forbidden_patterns = [
        "data/",
        "data\\",
        "raw_rag.db",
        ".wal",
        ".shm",
        "transcript.jsonl",
        ".cache",
        ".pytest_cache",
    ]

    # 2. Inspect Wheel
    latest_wheel = max(wheels, key=lambda p: p.stat().st_mtime)
    with zipfile.ZipFile(latest_wheel, "r") as z:
        wheel_names = z.namelist()

        # Check for forbidden sensitive or local cache items
        for name in wheel_names:
            for pattern in forbidden_patterns:
                assert pattern not in name, f"Forbidden pattern '{pattern}' found in wheel entry: {name}"

        # Check required files
        assert "agent_raw_rag/__init__.py" in wheel_names
        assert "agent_raw_rag/mcp_server.py" in wheel_names
        assert "agent_raw_rag/cli.py" in wheel_names
        assert "agent_raw_rag/resources/default_config.yaml" in wheel_names
        assert "agent_raw_rag/resources/com.user.agent-raw-rag.plist.template" in wheel_names

        # Check entry points
        entry_points_files = [n for n in wheel_names if n.endswith("entry_points.txt")]
        assert len(entry_points_files) == 1
        ep_content = z.read(entry_points_files[0]).decode("utf-8")
        assert "agent-raw-rag-mcp = agent_raw_rag.mcp_server:main" in ep_content
        assert "raw-rag-mcp = agent_raw_rag.mcp_server:main" in ep_content
        assert "raw-rag-ingest = agent_raw_rag.cli:main" in ep_content

        # Check metadata
        metadata_files = [n for n in wheel_names if n.endswith("METADATA")]
        assert len(metadata_files) == 1
        meta_content = z.read(metadata_files[0]).decode("utf-8")
        assert "Name: agent-raw-rag-mcp" in meta_content
        assert "License: MIT" in meta_content or "License-Expression: MIT" in meta_content

    # 3. Inspect Sdist (.tar.gz)
    latest_sdist = max(sdists, key=lambda p: p.stat().st_mtime)
    with tarfile.open(latest_sdist, "r:gz") as t:
        sdist_names = t.getnames()

        for name in sdist_names:
            for pattern in forbidden_patterns:
                assert pattern not in name, f"Forbidden pattern '{pattern}' found in sdist entry: {name}"

        # Verify essential code and configs exist in sdist
        assert any(n.endswith("pyproject.toml") for n in sdist_names)
        assert any("src/agent_raw_rag/resources/default_config.yaml" in n for n in sdist_names)
