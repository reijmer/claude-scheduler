# Claude Scheduler

An interactive terminal tool for scheduling recurring [Claude Code](https://docs.anthropic.com/en/docs/claude-code) tasks via cron. Define prompts, point them at project directories, set a schedule, and let Claude handle the rest.

**Example use cases:**
- Weekly codebase cleanup across multiple projects
- Daily Sentry error triage
- Nightly dependency audit
- Periodic PR review summaries

```
╭─ Claude Scheduler ─────────────────────────────╮
│                                                 │
│  3 jobs configured, 2 enabled                   │
│  Last run: sentry-check (2h ago, $0.1127)       │
│                                                 │
╰─────────────────────────────────────────────────╯

? What would you like to do?
❯ Add a new job
  View jobs
  Run a job now
  View run history
  Doctor (health check)
  Quit
```

## Install

```bash
# With pipx (recommended)
pipx install claude-scheduler

# Or with pip
pip install claude-scheduler
```

**Requirements:** Python 3.10+ and [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed.

## Quick Start

Just run:

```bash
claude-scheduler
```

You'll get an interactive menu. Select **Add a new job** and follow the prompts:

```
? Job name: weekly-cleanup
? Project directory: ~/Documents/my-project
? Prompt: Review for unused imports, dead code, and TODOs. Clean them up.
? Schedule: Weekly (Monday 9am)
? Allow dangerous permissions? No
? Model override:

✓ Job "weekly-cleanup" created
✓ Cron job installed: 0 9 * * 1
```

That's it. The job will run every Monday at 9am via cron, execute Claude Code in your project directory with the given prompt, and store the output for later review.

## Features

### Interactive job management

Navigate with arrow keys to add, edit, enable/disable, or delete jobs. View job details and run history without memorizing any flags.

```
? Select a job: weekly-cleanup

╭─ weekly-cleanup ────────────────────────────────╮
│ Prompt:    Review for unused imports, dead code…  │
│ Directory: ~/Documents/my-project                │
│ Schedule:  0 9 * * 1 (Mon 9:00)                  │
│ Perms:     normal                                 │
│ Model:     default                                │
╰───────────────────────────────────────────────────╯

? Action:
❯ Edit
  Run now
  View history
  Enable/Disable
  Delete
  Back
```

### Run history with cost tracking

Every run captures Claude's output, cost, duration, and exit status.

```
Run History: sentry-check
  #  │ Date             │ Duration │ Cost    │ Status
 ────┼──────────────────┼──────────┼─────────┼────────
  12 │ 2026-03-17 08:00 │ 45.2s    │ $0.1127 │ OK
  11 │ 2026-03-16 08:00 │ 38.1s    │ $0.0893 │ OK
  10 │ 2026-03-15 08:00 │ 52.4s    │ $0.1401 │ ERR
```

Select any run to view its full output.

### Schedule presets

Pick from common schedules or enter a custom cron expression:

| Preset | Cron |
|--------|------|
| Every hour | `0 * * * *` |
| Daily (9am) | `0 9 * * *` |
| Daily (8am) | `0 8 * * *` |
| Weekly (Monday 9am) | `0 9 * * 1` |
| Weekly (Friday 5pm) | `0 17 * * 5` |
| Every weekday (9am) | `0 9 * * 1-5` |
| Custom | any valid cron expression |

### Health check

Run the built-in doctor to verify your setup:

```
$ claude-scheduler doctor

Health Check

claude found: /opt/homebrew/bin/claude
claude-scheduler-runner found: /usr/local/bin/claude-scheduler-runner
Cron OK: weekly-cleanup
Cron OK: sentry-check
```

## Non-interactive commands

For scripting or quick access:

```bash
claude-scheduler run <job-name>       # Run a job immediately (foreground)
claude-scheduler list                 # List all jobs
claude-scheduler list --json          # List as JSON
claude-scheduler history <job-name>   # Show run history
claude-scheduler log <run-id>         # Show full output of a run
claude-scheduler doctor               # Health check
```

## How it works

1. **Jobs** are stored in a local SQLite database at `~/.claude-scheduler/scheduler.db`
2. **Cron entries** are installed in your user crontab, tagged with `# claude-scheduler:<name>` markers
3. When cron fires, it runs `claude-scheduler-runner <name>` which:
   - Loads the job config from SQLite
   - Acquires a file lock (prevents overlapping runs of the same job)
   - Executes `claude -p "<prompt>" --output-format json` in the job's directory
   - Parses the JSON response for cost, duration, and session info
   - Saves the full output to `~/.claude-scheduler/runs/<job>/<run-id>.json`
   - Records the run in the database

### Storage layout

```
~/.claude-scheduler/
├── scheduler.db              # Job definitions and run metadata
├── runs/                     # Full output of each run
│   ├── weekly-cleanup/
│   │   ├── 1.json
│   │   └── 2.json
│   └── sentry-check/
│       └── 1.json
├── locks/                    # Prevents concurrent runs
└── scheduler.log             # Runner diagnostics
```

## Development

```bash
git clone git@github.com:reijmer/claude-scheduler.git
cd claude-scheduler
uv venv && source .venv/bin/activate
uv pip install -e .
claude-scheduler --version
```

## License

MIT
