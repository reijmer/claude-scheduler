import shutil
import subprocess

from .models import Job

MARKER_PREFIX = "# claude-scheduler:"


def _read_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout


def _write_crontab(content: str) -> None:
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


def _marker(job_name: str) -> str:
    return f"{MARKER_PREFIX}{job_name}"


def _find_runner_path() -> str:
    path = shutil.which("claude-scheduler-runner")
    if path:
        return path
    return "claude-scheduler-runner"


def _build_cron_line(job: Job) -> str:
    runner = _find_runner_path()
    cmd = f"/bin/bash -lc '{runner} {job.name}'"
    return f"{job.schedule} {cmd}  {_marker(job.name)}"


def install_cron_job(job: Job) -> None:
    remove_cron_job(job.name)
    current = _read_crontab()
    new_line = _build_cron_line(job)
    if current and not current.endswith("\n"):
        current += "\n"
    current += new_line + "\n"
    _write_crontab(current)


def remove_cron_job(job_name: str) -> None:
    current = _read_crontab()
    if not current:
        return
    marker = _marker(job_name)
    lines = [line for line in current.splitlines() if marker not in line]
    _write_crontab("\n".join(lines) + "\n" if lines else "")


def disable_cron_job(job_name: str) -> None:
    current = _read_crontab()
    if not current:
        return
    marker = _marker(job_name)
    lines = []
    for line in current.splitlines():
        if marker in line and not line.startswith("#DISABLED# "):
            lines.append(f"#DISABLED# {line}")
        else:
            lines.append(line)
    _write_crontab("\n".join(lines) + "\n")


def enable_cron_job(job_name: str) -> None:
    current = _read_crontab()
    if not current:
        return
    marker = _marker(job_name)
    lines = []
    for line in current.splitlines():
        if marker in line and line.startswith("#DISABLED# "):
            lines.append(line[len("#DISABLED# "):])
        else:
            lines.append(line)
    _write_crontab("\n".join(lines) + "\n")


def list_cron_entries() -> list[str]:
    current = _read_crontab()
    if not current:
        return []
    return [line for line in current.splitlines() if MARKER_PREFIX in line]


def verify_cron_job(job_name: str) -> bool:
    marker = _marker(job_name)
    current = _read_crontab()
    for line in current.splitlines():
        if marker in line and not line.startswith("#DISABLED# "):
            return True
    return False
