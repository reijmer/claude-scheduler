"""CLI entry point. Interactive by default, with escape hatches for scripting."""

import json
import sys

from .config import ensure_dirs


def main() -> None:
    ensure_dirs()

    # Non-interactive escape hatches
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "run" and len(sys.argv) > 2:
            from .runner import run_job

            exit_code = run_job(sys.argv[2], foreground=True)
            sys.exit(exit_code)

        elif command == "list" and "--json" in sys.argv:
            from . import db

            jobs = db.list_jobs()
            data = [
                {
                    "name": j.name,
                    "prompt": j.prompt,
                    "directory": j.directory,
                    "schedule": j.schedule,
                    "model": j.model,
                    "skip_perms": j.skip_perms,
                    "enabled": j.enabled,
                }
                for j in jobs
            ]
            print(json.dumps(data, indent=2))
            sys.exit(0)

        elif command == "list":
            from . import db
            from .display import show_jobs_table

            jobs = db.list_jobs()
            show_jobs_table(jobs)
            sys.exit(0)

        elif command == "history" and len(sys.argv) > 2:
            from . import db
            from .display import show_run_history

            job = db.get_job_by_name(sys.argv[2])
            if not job:
                print(f"Job '{sys.argv[2]}' not found", file=sys.stderr)
                sys.exit(1)
            show_run_history(job)
            sys.exit(0)

        elif command == "log" and len(sys.argv) > 2:
            from . import db
            from .display import show_run_output

            run = db.get_run_by_id(int(sys.argv[2]))
            if not run:
                print(f"Run #{sys.argv[2]} not found", file=sys.stderr)
                sys.exit(1)
            show_run_output(run)
            sys.exit(0)

        elif command in ("--help", "-h", "help"):
            print_help()
            sys.exit(0)

        elif command == "--version":
            from . import __version__

            print(f"claude-scheduler {__version__}")
            sys.exit(0)

        else:
            print(f"Unknown command: {command}")
            print_help()
            sys.exit(1)

    # Default: interactive mode
    from .interactive import main_menu

    main_menu()


def print_help() -> None:
    help_text = """claude-scheduler - Interactive scheduler for recurring Claude Code tasks

Usage:
  claude-scheduler                     Launch interactive menu (default)
  claude-scheduler run <job-name>      Run a job immediately
  claude-scheduler list [--json]       List all jobs
  claude-scheduler history <job-name>  Show run history for a job
  claude-scheduler log <run-id>        Show full output of a run
  claude-scheduler --version           Show version
  claude-scheduler --help              Show this help"""
    print(help_text)
