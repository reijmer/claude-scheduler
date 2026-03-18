import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .db import get_latest_run, get_runs_for_job
from .models import Job, Run

console = Console()


def format_time_ago(dt: datetime) -> str:
    diff = datetime.now() - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def format_duration(ms: int | None) -> str:
    if ms is None:
        return "-"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"


def format_cost(cost: float | None) -> str:
    if cost is None:
        return "-"
    return f"${cost:.4f}"


def describe_schedule(cron_expr: str) -> str:
    parts = cron_expr.split()
    if len(parts) != 5:
        return cron_expr

    minute, hour, dom, month, dow = parts
    days = {"0": "Sun", "1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun"}

    if dom == "*" and month == "*" and dow == "*":
        return f"Daily {hour}:{minute.zfill(2)}"
    if dom == "*" and month == "*" and dow in days:
        return f"{days[dow]} {hour}:{minute.zfill(2)}"
    if dom == "*" and month == "*" and dow != "*":
        day_names = [days.get(d, d) for d in dow.split(",")]
        return f"{','.join(day_names)} {hour}:{minute.zfill(2)}"
    return cron_expr


def show_dashboard(jobs: list[Job]) -> None:
    enabled = sum(1 for j in jobs if j.enabled)
    total = len(jobs)

    last_run_info = ""
    for job in jobs:
        run = get_latest_run(job.id)
        if run and run.finished_at:
            ago = format_time_ago(run.finished_at)
            cost = format_cost(run.cost_usd)
            last_run_info = f"Last run: {job.name} ({ago}, {cost})"
            break

    lines = [f"  {total} jobs configured, {enabled} enabled"]
    if last_run_info:
        lines.append(f"  {last_run_info}")
    if not jobs:
        lines.append("  No jobs yet. Add one to get started!")

    console.print(Panel("\n".join(lines), title="Claude Scheduler", border_style="blue"))
    console.print()


def show_jobs_table(jobs: list[Job]) -> None:
    if not jobs:
        console.print("[dim]No jobs configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=3)
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Directory")
    table.add_column("Status")
    table.add_column("Last Run")

    for i, job in enumerate(jobs, 1):
        run = get_latest_run(job.id)
        status = "[green]enabled[/green]" if job.enabled else "[red]disabled[/red]"

        if run and run.finished_at:
            ago = format_time_ago(run.finished_at)
            icon = "[green]OK[/green]" if run.exit_code == 0 else "[red]ERR[/red]"
            last_run = f"{ago} {icon}"
        else:
            last_run = "[dim]never[/dim]"

        directory = job.directory.replace(str(Path.home()), "~")

        table.add_row(
            str(i),
            job.name,
            describe_schedule(job.schedule),
            directory,
            status,
            last_run,
        )

    console.print(table)


def show_job_detail(job: Job) -> None:
    prompt_display = job.prompt if len(job.prompt) <= 80 else job.prompt[:77] + "..."
    perms = "[red]skip permissions[/red]" if job.skip_perms else "normal"
    model = job.model or "default"
    directory = job.directory.replace(str(Path.home()), "~")

    lines = [
        f"Prompt:    {prompt_display}",
        f"Directory: {directory}",
        f"Schedule:  {job.schedule} ({describe_schedule(job.schedule)})",
        f"Perms:     {perms}",
        f"Model:     {model}",
        f"Status:    {'enabled' if job.enabled else 'disabled'}",
    ]
    console.print(Panel("\n".join(lines), title=job.name, border_style="cyan"))


def show_run_history(job: Job, limit: int = 20) -> None:
    runs = get_runs_for_job(job.id, limit=limit)
    if not runs:
        console.print(f"[dim]No runs recorded for '{job.name}'.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", title=f"Run History: {job.name}")
    table.add_column("#", style="dim", width=5)
    table.add_column("Date")
    table.add_column("Duration")
    table.add_column("Cost")
    table.add_column("Status")

    for run in runs:
        status = "[green]OK[/green]" if run.exit_code == 0 else f"[red]ERR ({run.exit_code})[/red]"
        if run.error:
            status = f"[red]{run.error[:30]}[/red]"
        table.add_row(
            str(run.id),
            run.started_at.strftime("%Y-%m-%d %H:%M"),
            format_duration(run.duration_ms),
            format_cost(run.cost_usd),
            status,
        )

    console.print(table)
    return


def show_run_output(run: Run) -> None:
    output_path = Path(run.output_file)
    if not output_path.exists():
        console.print("[red]Output file not found.[/red]")
        return

    raw = output_path.read_text()
    try:
        data = json.loads(raw)
        result_text = data.get("result", raw)
    except json.JSONDecodeError:
        result_text = raw

    cost = format_cost(run.cost_usd)
    duration = format_duration(run.duration_ms)
    header = f"Run #{run.id} | {run.started_at.strftime('%Y-%m-%d %H:%M')} | {duration} | {cost}"

    console.print(Panel(result_text, title=header, border_style="green" if run.exit_code == 0 else "red"))
