import os
import sys

import questionary
from rich.console import Console

from . import cron, db, display
from .runner import run_job

console = Console()

SCHEDULE_PRESETS = {
    "Every hour": "0 * * * *",
    "Daily (9am)": "0 9 * * *",
    "Daily (8am)": "0 8 * * *",
    "Weekly (Monday 9am)": "0 9 * * 1",
    "Weekly (Friday 5pm)": "0 17 * * 5",
    "Every weekday (9am)": "0 9 * * 1-5",
    "Custom cron expression": "custom",
}


def validate_name(name: str) -> bool | str:
    if not name.strip():
        return "Name cannot be empty"
    if " " in name:
        return "Name cannot contain spaces (use hyphens)"
    if db.get_job_by_name(name):
        return f"Job '{name}' already exists"
    return True


def validate_directory(path: str) -> bool | str:
    expanded = os.path.expanduser(path)
    if not os.path.isdir(expanded):
        return f"Directory does not exist: {expanded}"
    return True


def validate_cron(expr: str) -> bool | str:
    parts = expr.strip().split()
    if len(parts) != 5:
        return "Cron expression must have 5 fields (min hour dom month dow)"
    return True


def prompt_add_job() -> None:
    console.print("\n[bold]Add a new scheduled job[/bold]\n")

    name = questionary.text("Job name:", validate=validate_name).ask()
    if name is None:
        return

    directory = questionary.path(
        "Project directory:",
        default=os.getcwd(),
        only_directories=True,
        validate=validate_directory,
    ).ask()
    if directory is None:
        return
    directory = os.path.abspath(os.path.expanduser(directory))

    prompt = questionary.text(
        "Prompt (what should Claude do?):",
        multiline=False,
    ).ask()
    if prompt is None:
        return

    schedule_choice = questionary.select(
        "Schedule:",
        choices=list(SCHEDULE_PRESETS.keys()),
    ).ask()
    if schedule_choice is None:
        return

    if SCHEDULE_PRESETS[schedule_choice] == "custom":
        schedule = questionary.text("Cron expression (min hour dom month dow):", validate=validate_cron).ask()
        if schedule is None:
            return
    else:
        schedule = SCHEDULE_PRESETS[schedule_choice]

    skip_perms = questionary.confirm(
        "Allow dangerous permissions (--dangerously-skip-permissions)?",
        default=False,
    ).ask()
    if skip_perms is None:
        return

    model = questionary.text("Model override (leave blank for default):").ask()
    if model is not None:
        model = model.strip() or None

    # Create job
    try:
        job = db.add_job(
            name=name,
            prompt=prompt,
            directory=directory,
            schedule=schedule,
            model=model,
            skip_perms=skip_perms,
        )
    except Exception as e:
        console.print(f"[red]Failed to create job: {e}[/red]")
        return

    # Install cron
    try:
        cron.install_cron_job(job)
        console.print(f"\n[green]Job '{name}' created[/green]")
        console.print(f"[green]Cron job installed: {schedule}[/green]")
    except Exception as e:
        console.print(f"[yellow]Job created but cron installation failed: {e}[/yellow]")
        console.print("[yellow]You can still run it manually.[/yellow]")


def prompt_view_jobs() -> None:
    jobs = db.list_jobs()
    if not jobs:
        console.print("\n[dim]No jobs configured. Add one first![/dim]")
        return

    console.print()
    display.show_jobs_table(jobs)
    console.print()

    choices = [f"{j.name}" for j in jobs] + ["Back"]
    selected = questionary.select("Select a job (or back):", choices=choices).ask()
    if selected is None or selected == "Back":
        return

    job = db.get_job_by_name(selected)
    if not job:
        return

    prompt_job_actions(job)


def prompt_job_actions(job) -> None:
    display.show_job_detail(job)

    while True:
        action = questionary.select(
            "Action:",
            choices=["Edit", "Run now", "View history", "Enable" if not job.enabled else "Disable", "Delete", "Back"],
        ).ask()
        if action is None or action == "Back":
            return

        if action == "Run now":
            console.print()
            run_job(job.name, foreground=True)
            console.print()

        elif action == "View history":
            console.print()
            prompt_view_history(job)

        elif action == "Edit":
            prompt_edit_job(job)
            # Reload job
            job = db.get_job_by_name(job.name)
            if job:
                display.show_job_detail(job)

        elif action == "Enable":
            db.update_job(job.name, enabled=True)
            cron.enable_cron_job(job.name)
            console.print(f"[green]Job '{job.name}' enabled[/green]")
            job = db.get_job_by_name(job.name)

        elif action == "Disable":
            db.update_job(job.name, enabled=False)
            cron.disable_cron_job(job.name)
            console.print(f"[yellow]Job '{job.name}' disabled[/yellow]")
            job = db.get_job_by_name(job.name)

        elif action == "Delete":
            confirm = questionary.confirm(f"Delete job '{job.name}'? This cannot be undone.", default=False).ask()
            if confirm:
                cron.remove_cron_job(job.name)
                db.delete_job(job.name)
                console.print(f"[red]Job '{job.name}' deleted[/red]")
                return


def prompt_edit_job(job) -> None:
    field = questionary.select(
        "What to edit?",
        choices=["Prompt", "Directory", "Schedule", "Model", "Permissions", "Back"],
    ).ask()
    if field is None or field == "Back":
        return

    if field == "Prompt":
        new_val = questionary.text("New prompt:", default=job.prompt).ask()
        if new_val:
            db.update_job(job.name, prompt=new_val)
            console.print("[green]Prompt updated[/green]")

    elif field == "Directory":
        new_val = questionary.path("New directory:", default=job.directory, only_directories=True, validate=validate_directory).ask()
        if new_val:
            db.update_job(job.name, directory=os.path.abspath(os.path.expanduser(new_val)))
            console.print("[green]Directory updated[/green]")

    elif field == "Schedule":
        schedule_choice = questionary.select("New schedule:", choices=list(SCHEDULE_PRESETS.keys())).ask()
        if schedule_choice:
            if SCHEDULE_PRESETS[schedule_choice] == "custom":
                new_val = questionary.text("Cron expression:", validate=validate_cron).ask()
            else:
                new_val = SCHEDULE_PRESETS[schedule_choice]
            if new_val:
                db.update_job(job.name, schedule=new_val)
                updated_job = db.get_job_by_name(job.name)
                if updated_job:
                    cron.install_cron_job(updated_job)
                console.print("[green]Schedule updated[/green]")

    elif field == "Model":
        new_val = questionary.text("Model (blank for default):", default=job.model or "").ask()
        if new_val is not None:
            db.update_job(job.name, model=new_val.strip() or None)
            console.print("[green]Model updated[/green]")

    elif field == "Permissions":
        new_val = questionary.confirm("Skip permissions?", default=job.skip_perms).ask()
        if new_val is not None:
            db.update_job(job.name, skip_perms=new_val)
            console.print("[green]Permissions updated[/green]")


def prompt_run_job() -> None:
    jobs = db.list_jobs()
    if not jobs:
        console.print("\n[dim]No jobs configured.[/dim]")
        return

    choices = [j.name for j in jobs] + ["Back"]
    selected = questionary.select("Select a job to run:", choices=choices).ask()
    if selected is None or selected == "Back":
        return

    console.print()
    run_job(selected, foreground=True)
    console.print()


def prompt_view_history(job=None) -> None:
    if job is None:
        jobs = db.list_jobs()
        if not jobs:
            console.print("\n[dim]No jobs configured.[/dim]")
            return
        choices = [j.name for j in jobs] + ["Back"]
        selected = questionary.select("Select a job:", choices=choices).ask()
        if selected is None or selected == "Back":
            return
        job = db.get_job_by_name(selected)
        if not job:
            return

    display.show_run_history(job)

    runs = db.get_runs_for_job(job.id)
    if not runs:
        return

    choices = [f"#{r.id} - {r.started_at.strftime('%Y-%m-%d %H:%M')}" for r in runs] + ["Back"]
    selected = questionary.select("View run output (or back):", choices=choices).ask()
    if selected is None or selected == "Back":
        return

    run_id = int(selected.split("#")[1].split(" ")[0])
    run = db.get_run_by_id(run_id)
    if run:
        console.print()
        display.show_run_output(run)


def main_menu() -> None:
    jobs = db.list_jobs()
    display.show_dashboard(jobs)

    while True:
        action = questionary.select(
            "What would you like to do?",
            choices=[
                "Add a new job",
                "View jobs",
                "Run a job now",
                "View run history",
                "Quit",
            ],
        ).ask()

        if action is None or action == "Quit":
            console.print("[dim]Goodbye![/dim]")
            sys.exit(0)

        if action == "Add a new job":
            prompt_add_job()
        elif action == "View jobs":
            prompt_view_jobs()
        elif action == "Run a job now":
            prompt_run_job()
        elif action == "View run history":
            prompt_view_history()

        console.print()
