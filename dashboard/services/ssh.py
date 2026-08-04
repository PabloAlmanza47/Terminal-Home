"""Noninteractive and interactive OpenSSH command execution.

This module does not inspect a remote project or manage a workspace.  The
interactive API is intended for a later caller that attaches it to tmux.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

DEFAULT_CONNECTION_TIMEOUT_SECONDS = 10
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 30
DEFAULT_MAX_OUTPUT_CHARS = 64 * 1024

SshCommandStatus = Literal[
    "success",
    "command-failure",
    "connection-failure",
    "authentication-failure",
    "timeout",
    "missing-ssh",
]


@dataclass(frozen=True, slots=True)
class SshCommandResult:
    """The bounded result of one noninteractive SSH command."""

    status: SshCommandStatus
    stdout: str
    stderr: str
    returncode: int | None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether SSH completed successfully with an exit code of zero."""
        return self.status == "success" and self.returncode == 0


@dataclass(frozen=True, slots=True)
class SshInteractiveResult:
    """The exit status of an interactive SSH process with inherited streams."""

    status: SshCommandStatus
    returncode: int | None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether the interactive SSH process exited successfully."""
        return self.status == "success" and self.returncode == 0


def represent_remote_command(command: str) -> str:
    """Return a shell-escaped display representation of a remote command.

    This is for logs, errors, and other display contexts; it does not make
    arbitrary remote shell text safe.  Callers composing a structured remote
    command must quote each individual argument with
    :func:`quote_remote_argument` before joining those arguments.  SSH still
    receives the original composed command as one argv string.
    """
    return quote_remote_argument(command)


def quote_remote_argument(argument: str) -> str:
    """Quote one argument for a POSIX remote shell command string.

    This helper is for composing a structured command, not for validating or
    sanitizing arbitrary shell syntax.
    """
    return shlex.quote(argument)


def run_ssh_command(
    destination: str,
    command: str,
    *,
    connection_timeout: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> SshCommandResult:
    """Run *command* on an OpenSSH *destination* without an interactive prompt.

    ``destination`` and ``command`` remain single strings in the subprocess
    argv.  The remote command is interpreted by the remote shell, as it is by
    OpenSSH itself; this function never invokes a local shell.
    """
    if connection_timeout <= 0:
        raise ValueError("connection_timeout must be greater than zero")
    if execution_timeout <= 0:
        raise ValueError("execution_timeout must be greater than zero")
    if max_output_chars < 0:
        raise ValueError("max_output_chars must not be negative")

    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={connection_timeout}",
        destination,
        command,
    ]

    if shutil.which("ssh") is None:
        return SshCommandResult(
            status="missing-ssh",
            stdout="",
            stderr="",
            returncode=None,
            error="The ssh executable was not found on PATH.",
        )

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=execution_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _as_text(exc.stdout)
        stderr = _as_text(exc.stderr)
        stdout, stdout_truncated = _bound_output(stdout, max_output_chars)
        stderr, stderr_truncated = _bound_output(stderr, max_output_chars)
        return SshCommandResult(
            status="timeout",
            stdout=stdout,
            stderr=stderr,
            returncode=None,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            error=f"SSH command timed out after {execution_timeout} seconds.",
        )
    except FileNotFoundError:
        return SshCommandResult(
            status="missing-ssh",
            stdout="",
            stderr="",
            returncode=None,
            error="The ssh executable could not be started.",
        )
    except OSError as exc:
        return SshCommandResult(
            status="connection-failure",
            stdout="",
            stderr="",
            returncode=None,
            error=f"Could not start ssh: {exc}",
        )

    stdout, stdout_truncated = _bound_output(completed.stdout, max_output_chars)
    stderr, stderr_truncated = _bound_output(completed.stderr, max_output_chars)
    status = _classify_result(completed.returncode, completed.stderr)
    return SshCommandResult(
        status=status,
        stdout=stdout,
        stderr=stderr,
        returncode=completed.returncode,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def run_interactive_ssh(
    destination: str,
    command: str,
    *,
    request_tty: bool = True,
    connection_timeout: int = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
) -> SshInteractiveResult:
    """Run SSH with inherited terminal streams for a later tmux attachment.

    The destination and remote command are passed as separate argv entries;
    no local shell is involved.  The process is not captured, so its stdin,
    stdout, and stderr are inherited from the current process.  Credentials
    are neither requested nor managed here; OpenSSH's configured keys/agent
    and ``BatchMode`` determine authentication behavior.
    """
    if connection_timeout <= 0:
        raise ValueError("connection_timeout must be greater than zero")

    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={connection_timeout}",
    ]
    if request_tty:
        argv.append("-t")
    argv.extend([destination, command])

    if shutil.which("ssh") is None:
        return SshInteractiveResult(
            status="missing-ssh",
            returncode=None,
            error="The ssh executable was not found on PATH.",
        )

    try:
        completed = subprocess.run(
            argv,
            stdin=None,
            stdout=None,
            stderr=None,
        )
    except FileNotFoundError:
        return SshInteractiveResult(
            status="missing-ssh",
            returncode=None,
            error="The ssh executable could not be started.",
        )
    except OSError as exc:
        return SshInteractiveResult(
            status="connection-failure",
            returncode=None,
            error=f"Could not start ssh: {exc}",
        )

    return SshInteractiveResult(
        status=_classify_result(completed.returncode, ""),
        returncode=completed.returncode,
    )


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _bound_output(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _classify_result(returncode: int, stderr: str) -> SshCommandStatus:
    if returncode == 0:
        return "success"

    normalized = stderr.casefold()
    if any(
        marker in normalized
        for marker in (
            "permission denied (",
            "authentication failed",
            "authentication failure",
            "no supported authentication methods",
            "too many authentication failures",
        )
    ):
        return "authentication-failure"

    if returncode == 255 or any(
        marker in normalized
        for marker in (
            "could not resolve hostname",
            "connection refused",
            "connection timed out",
            "operation timed out",
            "no route to host",
            "connection reset by peer",
            "host key verification failed",
            "ssh_exchange_identification",
        )
    ):
        return "connection-failure"

    return "command-failure"
