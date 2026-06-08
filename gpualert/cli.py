"""
gpualert.cli — Command-line interface (Typer + Rich).

Every command that runs or monitors a job is responsible for:
  1. Collecting log file paths from the resulting JobResult
  2. Passing them through prepare_attachments()
  3. Handing the attachment list to the notifier
  4. Printing log paths to the user so they always know where logs live,
     even when the notification itself fails.
"""

from __future__ import annotations

from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gpualert import __version__
from gpualert.artifacts import find_artifacts, prepare_attachments
from gpualert.config import (
    GPUAlertConfig,
    get_config_path,
    load_config,
    validate_config,
)
from gpualert.config_manager import init_config_interactive
from gpualert.launcher import run_job
from gpualert.notifier.email_notifier import get_notifier
from gpualert.slurm import (
    SlurmNotAvailableError,
    is_slurm_available,
    poll_job,
)

app = typer.Typer(
    help="GPUAlert — GPU/Slurm job notifications with automatic log delivery.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _print_log_paths(result) -> None:
    console.print("\n[bold]Log files written:[/bold]")
    for lf in result.log_files():
        console.print(f"  [dim]-[/dim] {lf}")


def _print_result_table(result) -> None:
    status_color = "green" if result.is_success() else "red"
    table = Table(show_header=True, header_style="bold")
    table.add_column("Field", style="dim")
    table.add_column("Value")
    table.add_row(
        "Status",
        f"[{status_color}]{result.status.upper()}[/{status_color}]",
    )
    table.add_row("Duration", result.duration_human())
    table.add_row("Exit Code", str(result.exit_code))
    if result.error_summary:
        label = "Error" if result.is_failed() else "Metrics"
        first_line = result.error_summary.split("\n")[0][:80]
        table.add_row(label, first_line)
    console.print(table)


# ─── gpualert run ────────────────────────────────────────────────────────────
@app.command()
def run(
    cmd: List[str] = typer.Argument(..., help="Command to run."),
    attach: List[str] = typer.Option([], "--attach", "-a", help="Extra glob patterns to attach."),
    email_to: str = typer.Option("", "--email-to", "-e", help="Override recipient (one-off)."),
    timeout: Optional[int] = typer.Option(None, "--timeout", "-t", help="Timeout in seconds."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print notification, don't send."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Stream job output to console."),
    no_notify: bool = typer.Option(
        False, "--no-notify", help="Run without sending any notification."
    ),
):
    """Run a command and notify on completion. Logs always attached."""
    config = load_config()
    if email_to:
        config.email.to_addresses = [email_to]
    if dry_run:
        config.dry_run = True

    console.print(
        Panel(
            f"[bold cyan]GPUAlert[/bold cyan] v{__version__}\n"
            f"[dim]Command:[/dim] {' '.join(cmd)}\n"
            f"[dim]Logs   :[/dim] ~/.gpualert/logs/",
            title="Starting Job",
            border_style="cyan",
        )
    )

    with console.status("[bold yellow]Running job...[/bold yellow]", spinner="dots"):
        result = run_job(cmd, timeout=timeout, verbose=verbose)

    _print_result_table(result)
    _print_log_paths(result)

    if no_notify:
        console.print("[dim]Notification skipped (--no-notify)[/dim]")
        raise typer.Exit(0 if result.is_success() else 1)

    # Honor the attach_artifacts master toggle. When False, skip scanning
    # entirely so no output files are read or attached; logs still flow
    # through prepare_attachments per attach_logs_on_success. An explicit
    # NOTES line is added to the email body so the recipient never has to
    # guess why no artifacts were sent.
    if config.artifacts.attach_artifacts:
        artifact_list = find_artifacts(
            start_time=result.start_time,
            patterns=attach if attach else None,
        )
    else:
        artifact_list = []
        result.notes.append(
            "Artifact attachment disabled (artifacts.attach_artifacts=false). "
            "No output files were scanned or attached. Logs are still attached."
        )
        console.print(
            "[dim]Artifact attachment disabled by config "
            "(artifacts.attach_artifacts=false)[/dim]"
        )

    attach_files, _skipped = prepare_attachments(
        artifacts=artifact_list,
        log_files=result.log_files(),
        job_failed=result.is_failed(),
        attach_logs=config.email.attach_logs_on_success,
    )
    if artifact_list:
        console.print(f"\n[dim]Artifacts found: {len(artifact_list)}[/dim]")

    notifier = get_notifier(config, dry_run=dry_run)
    with console.status("[bold yellow]Sending notification...[/bold yellow]", spinner="dots"):
        note = notifier.send(result, attach_files)

    if note.success:
        console.print(f"\n[green]Email: {note.message}[/green]")
    else:
        console.print(f"\n[red]Notification failed: {note.message}[/red]")
        console.print("[dim]Logs are still saved locally (see paths above)[/dim]")

    raise typer.Exit(0 if result.is_success() else 1)


# ─── gpualert slurm ──────────────────────────────────────────────────────────
@app.command()
def slurm(
    job_id: int = typer.Argument(..., help="Slurm Job ID to monitor."),
    interval: int = typer.Option(10, "--interval", "-i", help="Poll interval (s)."),
    timeout: Optional[int] = typer.Option(None, "--timeout", help="Max wall time (s)."),
    email_to: str = typer.Option("", "--email-to", help="Override recipient."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print, don't send."),
):
    """Monitor a Slurm job by ID. Notifies on completion."""
    if not is_slurm_available():
        console.print("[red]Error:[/red] Slurm (sacct) not found in PATH.")
        console.print("Are you on a Slurm cluster? Use 'gpualert run' for local jobs.")
        raise typer.Exit(1)

    config = load_config()
    if email_to:
        config.email.to_addresses = [email_to]

    console.print(
        Panel(
            f"[bold cyan]GPUAlert[/bold cyan] — Slurm Monitor\n"
            f"[dim]Job ID :[/dim] {job_id}\n"
            f"[dim]Polling:[/dim] every {interval}s",
            title="Monitoring Slurm Job",
            border_style="cyan",
        )
    )

    def on_update(info):
        console.print(f"[dim][{info.state}][/dim] elapsed {info.elapsed_seconds:.0f}s")

    try:
        with console.status("[bold yellow]Waiting for Slurm job...[/bold yellow]"):
            result = poll_job(job_id, interval=interval, timeout=timeout, on_update=on_update)
    except SlurmNotAvailableError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    _print_log_paths(result)

    attach_files, _ = prepare_attachments(
        artifacts=[],
        log_files=result.log_files(),
        job_failed=result.is_failed(),
    )
    note = get_notifier(config, dry_run=dry_run).send(result, attach_files)
    if note.success:
        console.print(f"[green]Email: {note.message}[/green]")
    else:
        console.print(f"[red]{note.message}[/red]")

    raise typer.Exit(0 if result.is_success() else 1)


# ─── gpualert config ─────────────────────────────────────────────────────────
@app.command()
def config(
    init: bool = typer.Option(False, "--init", help="Interactive setup wizard."),
    show: bool = typer.Option(False, "--show", help="Show current configuration."),
    check: bool = typer.Option(False, "--check", help="Offline config validation (no network)."),
    test_email: bool = typer.Option(
        False, "--test-email", help="Send a test email (alias for 'gpualert test-email')."
    ),
    reset: bool = typer.Option(False, "--reset", help="Delete config file."),
):
    """Manage GPUAlert configuration."""
    cfg = load_config()

    if init:
        init_config_interactive(cfg)
        console.print("[green]Configuration saved.[/green]")
        return
    if show:
        console.print(
            Panel(cfg.safe_repr(), title="Current Config (password masked)", border_style="cyan")
        )
        return
    if check:
        ok, errors = validate_config(cfg)
        if ok:
            console.print("[green]Config is valid (offline check).[/green]")
            console.print("[dim]Run 'gpualert test-email' to verify SMTP actually works.[/dim]")
        else:
            console.print("[red]Config has problems:[/red]")
            for err in errors:
                console.print(f"  - {err}")
            raise typer.Exit(1)
        return
    if test_email:
        _send_test_email(cfg)
        return
    if reset:
        confirm = typer.confirm("Delete config file?")
        if confirm:
            p = get_config_path()
            if p.exists():
                p.unlink()
                console.print("[yellow]Config deleted.[/yellow]")
            else:
                console.print("[dim]No config file found.[/dim]")
        return

    console.print(
        "Use --init, --show, --check, --test-email, or --reset. " "Run 'gpualert config --help'."
    )


# ─── gpualert test-email (top-level) ─────────────────────────────────────────
@app.command("test-email")
def test_email_cmd():
    """Send a quick sanity-check email to verify SMTP config works."""
    _send_test_email(load_config())


def _send_test_email(cfg: GPUAlertConfig) -> None:
    """Shared body for both `config --test-email` and `test-email`."""
    import uuid
    from datetime import datetime

    from gpualert.types import JobResult

    ok, errors = validate_config(cfg)
    if not ok:
        console.print("[red]Config has problems:[/red]")
        for err in errors:
            console.print(f"  - {err}")
        console.print("\nRun: gpualert config --init")
        raise typer.Exit(1)

    probe = JobResult(
        command="gpualert test-email",
        job_id=str(uuid.uuid4()),
        start_time=datetime.now(),
        end_time=datetime.now(),
        duration_seconds=0,
        status="success",
        exit_code=0,
    )
    note = get_notifier(cfg).send(probe, [])
    if note.success:
        console.print("[green]Test email sent.[/green]")
        console.print(f"[dim]{note.message}[/dim]")
    else:
        console.print(f"[red]Test email failed: {note.message}[/red]")
        raise typer.Exit(1)


# ─── gpualert logs ───────────────────────────────────────────────────────────
@app.command()
def logs(
    n: int = typer.Option(10, "--last", "-n", help="Show last N job logs."),
):
    """Show recent job log directories."""
    from gpualert.log_manager import list_recent_logs

    recent = list_recent_logs(n)
    if not recent:
        console.print("[dim]No logs found. Run a job first.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Directory")
    table.add_column("Created")
    table.add_column("Size")
    for entry in recent:
        table.add_row(
            str(entry["dir"]),
            entry["created"].strftime("%Y-%m-%d %H:%M:%S") if entry.get("created") else "?",
            f"{entry.get('size_mb', 0):.2f} MB",
        )
    console.print(table)


# ─── gpualert version ────────────────────────────────────────────────────────
@app.command()
def version():
    """Print GPUAlert version."""
    console.print(f"gpualert {__version__}")


if __name__ == "__main__":
    app()
