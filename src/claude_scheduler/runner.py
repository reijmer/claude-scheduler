"""Runner entry point invoked by cron. Kept minimal for fast startup."""

import fcntl
import json
import shlex
import subprocess
import sys
import threading
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


def _process_event(event: dict, full_text: list) -> None:
    """Display a stream-json event to the terminal."""
    msg_type = event.get("type", "")

    if msg_type == "message":
        content = event.get("content", [])
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    print(text, end="", flush=True)
                    full_text.append(text)
            elif block_type == "tool_use":
                tool_name = block.get("name", "?")
                tool_input = block.get("input", {})
                if not isinstance(tool_input, dict):
                    tool_input = {}
                if tool_name == "Bash":
                    desc = tool_input.get("description", tool_input.get("command", ""))
                    print(f"\n> [{tool_name}] {desc}", flush=True)
                elif tool_name in ("Read", "Glob", "Grep"):
                    target = tool_input.get("file_path") or tool_input.get("pattern") or tool_input.get("path", "")
                    print(f"\n> [{tool_name}] {target}", flush=True)
                elif tool_name in ("Edit", "Write"):
                    target = tool_input.get("file_path", "")
                    print(f"\n> [{tool_name}] {target}", flush=True)
                else:
                    print(f"\n> [{tool_name}]", flush=True)
    elif msg_type == "result":
        result_text = event.get("result", "")
        if isinstance(result_text, str) and result_text and not full_text:
            print(result_text, flush=True)
            full_text.append(result_text)
    elif msg_type == "error":
        print(f"\n[ERROR] {event.get('error', event)}", file=sys.stderr, flush=True)
    elif msg_type == "system":
        msg = event.get("message") or event.get("subtype", "")
        if msg:
            print(f"[system] {msg}", flush=True)


def _run_foreground(job, cwd, run_id, output_file) -> int:
    """Run claude with stream-json, showing live output. Ctrl+C kills claude."""
    cmd = [
        "claude", "-p", job.prompt,
        "--output-format", "stream-json",
        "--verbose",
    ]
    if job.skip_perms:
        cmd.append("--dangerously-skip-permissions")
    if job.model:
        cmd.extend(["--model", job.model])

    print(f"Running job '{job.name}' in {job.directory}")
    print(f"Command: {shlex.join(cmd)}")
    print(f"Working directory: {cwd}")
    print()

    # Use a thread to read stdout so the main thread stays interruptible by Ctrl+C.
    # The subprocess shares our process group, so Ctrl+C sends SIGINT to both us and claude.
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    events = []
    full_text = []

    def _read_stdout():
        """Read stdout in a background thread, parse and display events."""
        buf = b""
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    events.append(event)
                    _process_event(event, full_text)
                except json.JSONDecodeError:
                    print(line, flush=True)
        # Process remaining buffer
        if buf.strip():
            line = buf.decode("utf-8", errors="replace").strip()
            try:
                event = json.loads(line)
                events.append(event)
                _process_event(event, full_text)
            except json.JSONDecodeError:
                print(line, flush=True)

    stderr_output = []

    def _read_stderr():
        """Read stderr in a background thread."""
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                break
            stderr_output.append(chunk)

    stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    # Main thread: wait for process. Ctrl+C raises KeyboardInterrupt here,
    # AND sends SIGINT to claude (same process group), so claude also exits.
    cancelled = False
    try:
        proc.wait()
    except KeyboardInterrupt:
        cancelled = True
        # claude already got SIGINT from the terminal. Give it a moment to exit.
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print("\n\nJob cancelled.")

    # Wait for reader threads to finish draining
    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)

    finished_at = datetime.now().isoformat()
    exit_code = proc.returncode

    # Extract cost/duration from the final result event
    cost_usd = None
    duration_ms = None
    session_id = None
    for event in reversed(events):
        if event.get("type") == "result":
            cost_usd = event.get("total_cost_usd")
            duration_ms = event.get("duration_ms")
            session_id = event.get("session_id")
            break

    # Save output
    output_data = {
        "mode": "foreground",
        "events": events,
        "full_text": "".join(str(t) for t in full_text),
        "exit_code": exit_code,
        "cancelled": cancelled,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
    }
    try:
        Path(output_file).write_text(json.dumps(output_data, indent=2, default=str))
    except Exception:
        Path(output_file).write_text(json.dumps({"error": "failed to serialize output"}, indent=2))

    stderr_text = b"".join(stderr_output).decode("utf-8", errors="replace").strip()
    if stderr_text:
        print(f"\n[stderr] {stderr_text}", file=sys.stderr)

    error_msg = None
    if cancelled:
        error_msg = "Cancelled by user (Ctrl+C)"
    elif exit_code != 0:
        error_msg = stderr_text or f"Exit code {exit_code}"

    # Print summary
    print()
    cost_str = f"${cost_usd:.4f}" if cost_usd else "unknown"
    dur_str = f"{duration_ms / 1000:.1f}s" if duration_ms else "unknown"
    status = "cancelled" if cancelled else ("OK" if exit_code == 0 else f"failed ({exit_code})")
    print(f"--- {status} | cost: {cost_str} | duration: {dur_str} ---")

    _save_run(
        run_id, output_file, finished_at, exit_code,
        cost_usd=cost_usd, duration_ms=duration_ms, session_id=session_id, error=error_msg,
    )

    return 130 if cancelled else exit_code


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
