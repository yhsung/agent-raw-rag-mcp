import os
import plistlib
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from agent_raw_rag.cli import (
    LAUNCH_AGENT_LABEL,
    install_launch_agent,
    uninstall_launch_agent,
    check_launch_agent_status,
)


def test_launchagent_template_validity():
    pkg_dir = Path(__file__).resolve().parent.parent
    template_path = pkg_dir / "configs" / f"{LAUNCH_AGENT_LABEL}.plist.template"
    assert template_path.exists(), f"Template should exist at {template_path}"

    content = template_path.read_text(encoding="utf-8")
    rendered = (
        content
        .replace("{{WORKING_DIR}}", "/fake/working/dir")
        .replace("{{PYTHON_EXEC}}", "/fake/venv/bin/python")
        .replace("{{INTERVAL}}", "20")
        .replace("{{PATH}}", "/usr/bin:/bin")
        .replace("{{LOG_DIR}}", "/fake/log/dir")
    )

    parsed = plistlib.loads(rendered.encode("utf-8"))
    assert parsed["Label"] == LAUNCH_AGENT_LABEL
    assert parsed["WorkingDirectory"] == "/fake/working/dir"
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] is True
    assert parsed["ProgramArguments"] == [
        "/fake/venv/bin/python",
        "-m",
        "agent_raw_rag.cli",
        "--watch",
        "--interval",
        "20",
    ]
    assert parsed["EnvironmentVariables"]["HF_HOME"] == "/Volumes/Samsung980/hf-home/"
    assert parsed["StandardOutPath"] == "/fake/log/dir/raw_rag_daemon.log"
    assert parsed["StandardErrorPath"] == "/fake/log/dir/raw_rag_daemon.err"


def test_install_and_uninstall_launch_agent(tmp_path: Path):
    fake_plist_path = tmp_path / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
    fake_log_dir = tmp_path / "logs"

    with patch("agent_raw_rag.cli.LAUNCH_AGENT_DIR", tmp_path / "LaunchAgents"), \
         patch("agent_raw_rag.cli.LAUNCH_AGENT_PLIST", fake_plist_path), \
         patch("agent_raw_rag.cli.DEFAULT_LOG_DIR", fake_log_dir), \
         patch("subprocess.run") as mock_subproc:

        mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # 1. Install
        ret = install_launch_agent(interval=30)
        assert ret == 0
        assert fake_plist_path.exists()
        parsed = plistlib.loads(fake_plist_path.read_bytes())
        assert parsed["Label"] == LAUNCH_AGENT_LABEL

        # 2. Status
        mock_subproc.return_value = MagicMock(
            returncode=0,
            stdout=f"12345 0 {LAUNCH_AGENT_LABEL}\n",
            stderr="",
        )
        ret_status = check_launch_agent_status()
        assert ret_status == 0

        # 3. Uninstall
        ret_uninst = uninstall_launch_agent()
        assert ret_uninst == 0
        assert not fake_plist_path.exists()
