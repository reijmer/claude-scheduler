"""Runner entry point invoked by cron. Kept minimal for fast startup."""

import fcntl
import json
import subprocess
import sys
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

    # Create placeholder run record
    output_file = str(job_runs_dir / "pending.json")
    run_id = insert_run(job_id=job.id, started_at=started_at, output_file=output_file)

    # Update output file path with actual run ID
    output_file = str(job_runs_dir / f"{run_id}.json")

    # Build claude command
    cmd = ["claude", "-p", job.prompt, "--output-format", "json"]
    if job.skip_perms:
        cmd.append("--dangerously-skip-permissions")
    if job.model:
        cmd.extend(["--model", job.model])

    cwd = Path(job.directory)
    if not cwd.is_dir():
        error_msg = f"Working directory does not exist: {job.directory}"
        update_run(run_id, finished_at=datetime.now().isoformat(), exit_code=1, error=error_msg)
        print(f"Error: {error_msg}", file=sys.stderr)
        return 1

    if foreground:
        print(f"Running job '{job_name}' in {job.directory}...")
        print(f"Prompt: {job.prompt[:100]}{'...' if len(job.prompt) > 100 else ''}")
        print()

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max
        )
    except subprocess.TimeoutExpired:
        error_msg = "Job timed out after 1 hour"
        update_run(run_id, finished_at=datetime.now().isoformat(), exit_code=-1, error=error_msg)
        print(f"Error: {error_msg}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        error_msg = "claude command not found. Is Claude Code installed?"
        update_run(run_id, finished_at=datetime.now().isoformat(), exit_code=-1, error=error_msg)
        print(f"Error: {error_msg}", file=sys.stderr)
        return 1

    finished_at = datetime.now().isoformat()

    # Save raw output
    Path(output_file).write_text(result.stdout or result.stderr or "")

    # Parse JSON output
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

            if foreground:
                # Print the actual result text
                print(data.get("result", result.stdout))
        except json.JSONDecodeError:
            if foreground:
                print(result.stdout)

    if result.returncode != 0 and not error:
        error = result.stderr or f"Exit code {result.returncode}"

    if foreground and error:
        print(f"\nError: {error}", file=sys.stderr)

    update_run(
        run_id,
        finished_at=finished_at,
        exit_code=result.returncode,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        session_id=session_id,
        error=error,
    )

    # Update the output file path in the DB
    from .db import get_connection

    conn = get_connection()
    try:
        conn.execute("UPDATE runs SET output_file = ? WHERE id = ?", (output_file, run_id))
        conn.commit()
    finally:
        conn.close()

    if foreground:
        cost_str = f"${cost_usd:.4f}" if cost_usd else "unknown"
        dur_str = f"{duration_ms / 1000:.1f}s" if duration_ms else "unknown"
        print(f"\nCompleted: cost={cost_str}, duration={dur_str}, exit_code={result.returncode}")

    return result.returncode


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: claude-scheduler-runner <job-name>", file=sys.stderr)
        sys.exit(1)
    job_name = sys.argv[1]
    exit_code = run_job(job_name, foreground=False)
    sys.exit(exit_code)
