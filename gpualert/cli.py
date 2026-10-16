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

import os
from typing import List, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from gpualert import __version__
from gpualert.artifacts import describe_skipped, find_artifacts, prepare_attachments
from gpualert.config import (
    GPUAlertConfig,
    get_config_path,
    load_config,
    validate_config,
)
from gpualert.config_manager import init_config_interactive
from gpualert.launcher import run_job
from gpualert.log_manager import log_dir_label
from gpualert.notifier.email_notifier import get_notifier
from gpualert.safe_io import resolve_roots
from gpualert.slurm import (
    SlurmNotAvailableError,
    is_slurm_available,
    poll_job,
)
from gpualert.types import JobResult

app = typer.Typer(
    help="GPUAlert — GPU/Slurm job notifications with automatic log delivery.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _print_log_paths(result) -> None:
    console.print("\n[bold]Log files written:[/bold]")
    for lf in result.log_files():
        console.print(f"  [dim]-[/dim] {escape(str(lf))}")
    # Notes from the run itself (log fallback, decode replacements, capture
    # problems). Shown here too, so --no-notify users see them.
    for note in result.notes or []:
        # Notes can carry file names from the job; escape them so a name
        # like "[/]" is printed, not parsed as Rich markup.
        console.print(f"[yellow]Note:[/yellow] {escape(str(note))}", highlight=False)


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
        table.add_row(label, escape(first_line))
    console.print(table)


# ─── gpualert run ────────────────────────────────────────────────────────────


def _cwd_or_empty() -> str:
    try:
        return os.getcwd()
    except OSError:
        return ""


def _shell_exit_code(result: JobResult) -> int:
    """Map a JobResult to the exit status a shell or Slurm script expects.

    The wrapped job's own code is passed through so `gpualert run -- cmd`
    can replace `cmd` in pipelines. Killed by signal N gives 128+N, timeout
    gives 124 (as GNU timeout does), Ctrl+C gives 130, and an internal
    launcher error gives 1. Notification failures never change this value.
    """
    if result.status == "timeout":
        return 124
    if result.status == "interrupted":
        return 130
    code = result.exit_code
    if code is None or code == -99:
        return 0 if result.is_success() else 1
    if code < 0:
        return min(128 + (-code), 255)
    if code == 0 and not result.is_success():
        # A failed job must never look like success to the calling script.
        # Slurm reports 0 for NODE_FAIL, PREEMPTED and some cancellations.
        return 1
    return code if code <= 255 else 1


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

    # Console output is a convenience. If it fails (stdout closed, a broken
    # pipe), the job still runs and its status still comes back.
    _quietly(
        lambda: console.print(
            Panel(
                f"[bold cyan]GPUAlert[/bold cyan] v{__version__}\n"
                f"[dim]Command:[/dim] {escape(' '.join(cmd))}\n"
                f"[dim]Logs   :[/dim] {escape(log_dir_label())}",
                title="Starting Job",
                border_style="cyan",
            )
        )
    )

    spinner = _quietly(lambda: console.status("[bold yellow]Running job...[/bold yellow]"))
    if spinner is not None and _quietly(spinner.__enter__) is None:
        spinner = None
    try:
        result = run_job(cmd, timeout=timeout, verbose=verbose)
    finally:
        if spinner is not None:
            _quietly(lambda: spinner.__exit__(None, None, None))

    # Fixed here, before any reporting: nothing after this line may change
    # it, which is what lets a dead mail server or an odd file name never
    # look like a failed job.
    exit_status = _shell_exit_code(result)
    try:
        _report_run(result, config, attach, dry_run, no_notify)
    except (Exception, KeyboardInterrupt) as exc:
        message = (
            f"[red]Reporting failed after the job finished "
            f"({escape(type(exc).__name__)}: {escape(str(exc))}). "
            "The exit status is the job's own; its logs are on disk.[/red]"
        )
        _quietly(lambda: console.print(message))
    raise typer.Exit(exit_status)


def _quietly(fn):
    try:
        return fn()
    except Exception:
        return None


def _report_run(result, config, attach, dry_run: bool, no_notify: bool) -> None:
    """Everything after the job: tables, artifact scan, notification."""
    _print_result_table(result)
    _print_log_paths(result)

    if no_notify:
        console.print("[dim]Notification skipped (--no-notify)[/dim]")
        return

    # Honor the attach_artifacts master toggle. When False, skip scanning
    # entirely so no output files are read or attached; logs still flow
    # through prepare_attachments per attach_logs_on_success. An explicit
    # NOTES line is added to the email body so the recipient never has to
    # guess why no artifacts were sent.
    scan_root = result.cwd or _cwd_or_empty()
    # The notifier only reads attachments from inside these directories.
    result.cwd = scan_root
    if config.artifacts.attach_artifacts and scan_root:
        excluded: dict = {}
        artifact_list = find_artifacts(
            start_time=result.start_time,
            cwd=scan_root,
            patterns=attach if attach else None,
            max_single_mb=config.artifacts.max_single_file_mb,
            max_depth=config.artifacts.scan_depth,
            end_time=result.end_time,
            skipped=excluded,
        )
        result.notes.extend(
            describe_skipped(excluded, max_single_mb=config.artifacts.max_single_file_mb)
        )
    elif config.artifacts.attach_artifacts:
        artifact_list = []
        result.notes.append(
            "The job's working directory could not be determined, so no output files "
            "were scanned. Logs are still attached."
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

    attach_files, over_budget = prepare_attachments(
        artifacts=artifact_list,
        log_files=result.log_files(),
        job_failed=result.is_failed(),
        attach_logs=config.email.attach_logs_on_success,
        max_total_mb=config.artifacts.max_total_mb,
        roots=resolve_roots([scan_root] + [os.path.dirname(p) for p in result.log_files()]),
    )
    if over_budget:
        names = [os.path.basename(p) for p in over_budget if p]
        shown = ", ".join(names[:10]) + (f" and {len(names) - 10} more" if len(names) > 10 else "")
        result.notes.append(f"Not attached, over the email size budget: {shown}.")
    if artifact_list:
        console.print(f"\n[dim]Artifacts found: {len(artifact_list)}[/dim]")

    # Extract structured-file metrics (CSV/JSON/YAML/XLSX) for the email
    # body. This is best-effort and never blocks the notification.
    if artifact_list:
        try:
            from gpualert.metrics import extract_metrics, format_metrics_line

            metrics = extract_metrics([a.path for a in artifact_list])
            if metrics:
                result.notes.append(f"Metrics: {format_metrics_line(metrics)}")
                console.print(f"[dim]Metrics: {format_metrics_line(metrics)}[/dim]")
        except Exception:
            pass  # notifier isolation: metric parse errors never touch the job

    notifier = get_notifier(config, dry_run=dry_run)
    with console.status("[bold yellow]Sending notification...[/bold yellow]", spinner="dots"):
        note = notifier.send(result, attach_files)

    if note.success:
        console.print(f"\n[green]Email: {escape(note.message)}[/green]")
    else:
        console.print(f"\n[red]Notification failed: {escape(note.message)}[/red]")
        console.print("[dim]Logs are still saved locally (see paths above)[/dim]")


# ─── gpualert slurm ──────────────────────────────────────────────────────────
@app.command()
def slurm(
    job_id: int = typer.Argument(..., help="Slurm Job ID to monitor."),
    interval: int = typer.Option(
        10, "--interval", "-i", min=1, help="Poll interval in seconds (at least 1)."
    ),
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
        console.print(f"[dim]\\[{escape(info.state)}][/dim] elapsed {info.elapsed_seconds:.0f}s")

    try:
        with console.status("[bold yellow]Waiting for Slurm job...[/bold yellow]"):
            result = poll_job(job_id, interval=interval, timeout=timeout, on_update=on_update)
    except SlurmNotAvailableError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1)

    exit_status = _shell_exit_code(result)
    try:
        _print_log_paths(result)
        attach_files, _ = prepare_attachments(
            artifacts=[],
            log_files=result.log_files(),
            job_failed=result.is_failed(),
        )
        note = get_notifier(config, dry_run=dry_run).send(result, attach_files)
        if note.success:
            console.print(f"[green]Email: {escape(note.message)}[/green]")
        else:
            console.print(f"[red]{escape(note.message)}[/red]")
    except (Exception, KeyboardInterrupt) as exc:
        message = f"[red]Reporting failed ({escape(type(exc).__name__)}: {escape(str(exc))}).[/red]"
        _quietly(lambda: console.print(message))
    raise typer.Exit(exit_status)


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
                console.print(f"  - {escape(str(err))}")
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
            console.print(f"  - {escape(str(err))}")
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
        console.print(f"[dim]{escape(note.message)}[/dim]")
    else:
        console.print(f"[red]Test email failed: {escape(note.message)}[/red]")
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
            escape(str(entry["dir"])),
            entry["created"].strftime("%Y-%m-%d %H:%M:%S") if entry.get("created") else "?",
            f"{entry.get('size_mb', 0):.2f} MB",
        )
    console.print(table)


# ─── gpualert version ────────────────────────────────────────────────────────
@app.command()
def version():
    """Print GPUAlert version."""
    console.print(f"gpualert {__version__}")


# ─── gpualert uninstall / purge ──────────────────────────────────────────────
@app.command()
def uninstall(
    keep_logs: bool = typer.Option(False, "--keep-logs", help="Preserve ~/.gpualert/logs/."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Delete all GPUAlert secrets, config, and (optionally) logs.

    Because pip does not run any code on `pip uninstall`, `~/.gpualert/`
    survives package removal. Run this BEFORE `pip uninstall gpualert`.
    """
    _run_purge(keep_logs=keep_logs, yes=yes)


@app.command()
def purge(
    keep_logs: bool = typer.Option(False, "--keep-logs", help="Preserve ~/.gpualert/logs/."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Alias for `gpualert uninstall`."""
    _run_purge(keep_logs=keep_logs, yes=yes)


def _run_purge(keep_logs: bool, yes: bool) -> None:
    import shutil
    from pathlib import Path

    cfg_dir = Path.home() / ".gpualert"

    if not yes:
        console.print(
            "[yellow]About to delete all GPUAlert secrets and config under "
            f"{escape(str(cfg_dir))}.[/yellow]"
        )
        if keep_logs:
            console.print("[dim]Logs will be preserved (--keep-logs).[/dim]")
        if not typer.confirm("Proceed?"):
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(1)

    try:
        cfg = load_config()
        username = cfg.smtp.username or ""
    except Exception:
        username = ""

    try:
        from gpualert import secrets as gsecrets

        if username:
            gsecrets.purge_keyring(username)
        gsecrets.purge_file_secret()
    except ImportError:
        pass

    if not cfg_dir.exists():
        console.print("[green]Secrets purged. No config directory to remove.[/green]")
        console.print("[dim]Now run:  pip uninstall gpualert[/dim]")
        return

    if keep_logs:
        logs_dir = cfg_dir / "logs"
        for child in cfg_dir.iterdir():
            if child == logs_dir:
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                pass
        console.print(
            f"[green]Secrets and config removed.[/green] Logs preserved at {escape(str(logs_dir))}."
        )
    else:
        shutil.rmtree(cfg_dir, ignore_errors=True)
        console.print(f"[green]Removed {escape(str(cfg_dir))} and all secrets.[/green]")

    console.print("[dim]Now run:  pip uninstall gpualert[/dim]")


if __name__ == "__main__":
    app()
