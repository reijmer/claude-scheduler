"""Runner entry point invoked by cron. Kept minimal for fast startup."""

import fcntl
import json
import os
import shlex
import subprocess
import sys
import termios
from datetime import datetime
from pathlib import Path

from .config import LOCKS_DIR, RUNS_DIR, ensure_dirs
from .db import get_job_by_name, insert_run, update_run


def _acquire_lock(job_name: str) -> int | None:
    lock_path = LOCKS_DIR / f"{job_name}.lock"
    fd = open(lock_path, "w")  # noqa: SIM115
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd.fileno()
    except OSError:
        fd.close()
        return None


def _save_run(run_id: int, output_file: str, finished_at: str, exit_code: int, **kwargs) -> None:
    update_run(run_id, finished_at=finished_at, exit_code=exit_code, **kwargs)
    from .db import get_connection

    conn = get_connection()
    try:
        conn.execute("UPDATE runs SET output_file = ? WHERE id = ?", (output_file, run_id))
        conn.commit()
    finally:
        conn.close()


def _reset_terminal():
    """Reset terminal to sane state after questionary/prompt_toolkit."""
    try:
        fd = sys.stdin.fileno()
        # Save and restore terminal attributes to cooked mode
        attrs = termios.tcgetattr(fd)
        # Ensure ECHO and ICANON are on (cooked mode)
        attrs[3] |= termios.ECHO | termios.ICANON
        # Ensure ISIG is on (so Ctrl+C generates SIGINT)
        attrs[3] |= termios.ISIG
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except (termios.error, ValueError, OSError):
        # Not a terminal (e.g. piped), skip
        pass
    # Also run stty sane as a belt-and-suspenders fix
    try:
        subprocess.run(["stty", "sane"], stdin=sys.stdin, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _run_foreground(job, cwd, run_id, output_file) -> int:
    """Run claude directly via os.system() for full terminal passthrough."""
    cmd_parts = [
        "claude", "-p", shlex.quote(job.prompt),
        "--output-format", "stream-json",
        "--verbose",
    ]
    if job.skip_perms:
        cmd_parts.append("--dangerously-skip-permissions")
    if job.model:
        cmd_parts.extend(["--model", shlex.quote(job.model)])

    shell_cmd = " ".join(cmd_parts)

    # Reset terminal to sane state -- questionary/prompt_toolkit may have
    # left it in raw mode with signals disabled
    _reset_terminal()

    print(f"Running job '{job.name}' in {job.directory}")
    print(f"Command: claude -p '...' --output-format stream-json --verbose" + (" --dangerously-skip-permissions" if job.skip_perms else ""))
    print(f"Working directory: {cwd}")
    print(f"Ctrl+C to cancel. Output is raw JSON (stream-json mode).")
    print()
    sys.stdout.flush()
    sys.stderr.flush()

    # Use os.system() which runs through /bin/sh and gives claude full
    # terminal access. stream-json output goes directly to the terminal
    # so the user sees progress events as they happen.
    saved_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        exit_status = os.system(shell_cmd)
        exit_code = os.waitstatus_to_exitcode(exit_status) if hasattr(os, 'waitstatus_to_exitcode') else (exit_status >> 8)
    except Exception as e:
        print(f"Error running command: {e}", file=sys.stderr)
        _save_run(run_id, output_file, datetime.now().isoformat(), 1, error=str(e))
        return 1
    finally:
        os.chdir(saved_cwd)

    cancelled = exit_code == 130 or exit_code < 0  # SIGINT

    finished_at = datetime.now().isoformat()

    summary = {
        "mode": "foreground",
        "exit_code": exit_code,
        "cancelled": cancelled,
        "prompt": job.prompt,
        "directory": job.directory,
    }
    Path(output_file).write_text(json.dumps(summary, indent=2))

    error_msg = None
    if cancelled:
        error_msg = "Cancelled by user (Ctrl+C)"
    elif exit_code != 0:
        error_msg = f"Exit code {exit_code}"

    print()
    status = "cancelled" if cancelled else ("OK" if exit_code == 0 else f"failed ({exit_code})")
    print(f"--- {status} ---")

    _save_run(run_id, output_file, finished_at, exit_code, error=error_msg)

    return exit_code


def _run_background(job, cmd, cwd, run_id, output_file) -> int:
    """Run claude with json output, capturing everything for cron."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        _save_run(run_id, output_file, datetime.now().isoformat(), -1, error="Job timed out after 1 hour")
        return 1
    except FileNotFoundError:
        _save_run(run_id, output_file, datetime.now().isoformat(), -1, error="claude command not found")
        return 1

    finished_at = datetime.now().isoformat()
    Path(output_file).write_text(result.stdout or result.stderr or "")

    cost_usd = None
    duration_ms = None
    session_id = None
    error = None

    if result.stdout:
        try:
            data = json.loads(result.stdout)
            cost_usd = data.get("total_cost_usd")
            duration_ms = data.get("duration_ms")
            session_id = data.get("session_id")
            if data.get("is_error"):
                error = data.get("result", "Unknown error")
        except json.JSONDecodeError:
            pass

    if result.returncode != 0 and not error:
        error = result.stderr or f"Exit code {result.returncode}"

    _save_run(
        run_id, output_file, finished_at, result.returncode,
        cost_usd=cost_usd, duration_ms=duration_ms, session_id=session_id, error=error,
    )
    return result.returncode


def run_job(job_name: str, foreground: bool = False) -> int:
    ensure_dirs()

    job = get_job_by_name(job_name)
    if not job:
        print(f"Error: job '{job_name}' not found", file=sys.stderr)
        return 1

    if not foreground:
        lock_fd = _acquire_lock(job_name)
        if lock_fd is None:
            print(f"Job '{job_name}' is already running, skipping", file=sys.stderr)
            return 0

    job_runs_dir = RUNS_DIR / job_name
    job_runs_dir.mkdir(exist_ok=True)

    started_at = datetime.now().isoformat()
    output_file = str(job_runs_dir / "pending.json")
    run_id = insert_run(job_id=job.id, started_at=started_at, output_file=output_file)
    output_file = str(job_runs_dir / f"{run_id}.json")

    cwd = Path(job.directory)
    if not cwd.is_dir():
        error_msg = f"Working directory does not exist: {job.directory}"
        _save_run(run_id, output_file, datetime.now().isoformat(), 1, error=error_msg)
        print(f"Error: {error_msg}", file=sys.stderr)
        return 1

    if foreground:
        return _run_foreground(job, cwd, run_id, output_file)

    # Background: use --output-format json for structured output
    cmd = ["claude", "-p", job.prompt, "--output-format", "json"]
    if job.skip_perms:
        cmd.append("--dangerously-skip-permissions")
    if job.model:
        cmd.extend(["--model", job.model])

    return _run_background(job, cmd, cwd, run_id, output_file)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: claude-scheduler-runner <job-name>", file=sys.stderr)
        sys.exit(1)
    job_name = sys.argv[1]
    exit_code = run_job(job_name, foreground=False)
    sys.exit(exit_code)
