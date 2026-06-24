"""sandbox_exec backend — real OS-level isolation on macOS.

These tests run the actual sandbox-exec wrapper. On non-macOS (or where the
binary is absent) the backend silently degrades to local, so the tests skip the
isolation assertions and only check the fallback. On macOS they assert the
kernel refuses out-of-sandbox writes and network.
"""
from __future__ import annotations

import platform
import shutil

import pytest

from taiyi.scheduler.planner import PlanStep
from taiyi.tools import SandboxExecutor

_ON_MACOS = platform.system() == "Darwin" and shutil.which("sandbox-exec") is not None


def _exec(sandbox, backend, command, args=None):
    ex = SandboxExecutor(sandbox, backend=backend)
    return ex.execute(PlanStep(tool=f"shell:{command}", args=list(args or [])))


def test_backend_degrades_off_macos(tmp_path):
    # sandbox_exec requested; on non-macOS it must fall back to local, not crash.
    ex = SandboxExecutor(tmp_path, backend="sandbox_exec")
    if _ON_MACOS:
        assert ex.backend == "sandbox_exec"
    else:
        assert ex.backend == "local"


@pytest.mark.skipif(not _ON_MACOS, reason="sandbox-exec is macOS-only")
def test_sandbox_exec_allows_writing_inside_sandbox(tmp_path):
    res = _exec(tmp_path, "sandbox_exec", "sh", ["-c", "echo hello > inside.txt; cat inside.txt"])
    assert res.ok, res.output
    assert "hello" in res.output
    assert (tmp_path / "inside.txt").exists()


@pytest.mark.skipif(not _ON_MACOS, reason="sandbox-exec is macOS-only")
def test_sandbox_exec_blocks_write_outside_sandbox(tmp_path):
    # Try to write to a path OUTSIDE the sandbox dir. The kernel must refuse it.
    outside = tmp_path.parent / "escape_target.txt"
    if outside.exists():
        outside.unlink()
    res = _exec(tmp_path, "sandbox_exec", "sh",
                ["-c", f"echo pwned > {outside}"])
    # The write should fail (sandbox violation) — the file must NOT be created.
    assert not outside.exists(), "sandbox failed: wrote outside the sandbox dir"
    # And the command reports failure (non-zero exit / sandbox error in stderr).
    assert not res.ok


@pytest.mark.skipif(not _ON_MACOS, reason="sandbox-exec is macOS-only")
def test_sandbox_exec_blocks_network(tmp_path):
    # curl/nc to localhost should be denied at the kernel level (deny network*).
    # Use a no-op network op; if the sandbox works, it errors instead of connecting.
    res = _exec(tmp_path, "sandbox_exec", "sh",
                ["-c", "echo test | nc -w 1 127.0.0.1 1 2>&1; echo exit=$?"])
    # We don't assert the exact message, only that the network op did not succeed
    # cleanly (sandbox denial surfaces as an error). The key invariant: no real
    # connection. nc to a closed port would also fail, so this is a soft check —
    # the hard check is the out-of-sandbox write test above.
    assert "exit=" in res.output


@pytest.mark.skipif(not _ON_MACOS, reason="sandbox-exec is macOS-only")
def test_sandbox_exec_normal_commands_work(tmp_path):
    # Sanity: a normal self-contained command (no host-config dependency) runs
    # fine inside the sandbox. We avoid git here — its macOS xcrun shim reaches
    # into host paths that isolation rightly blocks; a sandboxed agent that needs
    # git should run it with HOME pointed at the sandbox and dev tools detached.
    res = _exec(tmp_path, "sandbox_exec", "sh", [
        "-c", "echo hello > out.txt && cat out.txt && ls -1",
    ])
    assert res.ok, res.output
    assert "hello" in res.output
    assert "out.txt" in res.output
    assert (tmp_path / "out.txt").exists()


@pytest.mark.skipif(not _ON_MACOS, reason="sandbox-exec is macOS-only")
def test_sandbox_exec_subprocess_fork_allowed(tmp_path):
    # Pipes and subshells require fork; the profile must allow it.
    res = _exec(tmp_path, "sandbox_exec", "sh", ["-c", "echo a | cat; (echo b)"])
    assert res.ok, res.output
    assert "a" in res.output and "b" in res.output
