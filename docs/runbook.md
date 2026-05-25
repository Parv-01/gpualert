# Runbook

Common operational problems and how to recover. Every entry assumes you've already run
`gpualert config --check` to rule out a malformed config.

## `gpualert test-email` fails with SMTP authentication error

```
Test email failed: SMTP authentication failed. Check username/password. For Gmail, use an App Password.
```

For Gmail: regular account passwords no longer work over SMTP. You need an App Password.

1. Enable 2-Step Verification on the Google account.
2. Visit <https://myaccount.google.com/apppasswords>.
3. Generate a 16-character App Password.
4. Paste it (no spaces) into `gpualert config --init` at the password prompt.

For other providers: confirm the account allows SMTP submission, and that the username is the
literal address (not a display name). Workplace Outlook tenants often disable basic SMTP — you'll
need IT to enable it or switch to a different account.

## Connection refused / timeout on SMTP

```
Connection refused to smtp.gmail.com:587. Check server settings.
```

Most often a firewall or VPN blocking outbound port 587. Confirm with:

```bash
nc -vz smtp.gmail.com 587
```

If `nc` hangs or fails, GPUAlert will too. Either open the port or run from a host that can reach
the SMTP server. The job itself is not affected — logs are still on disk at `~/.gpualert/logs/`.

## Job finished but no email arrived

In order:

1. Check the CLI output. The line that follows the run is either
   `Email: Email sent to [...]` or `Notification failed: ...`. If it says `Notification failed`,
   the message after the colon tells you why.
2. Check spam / promotions folder. Gmail aggressively buckets self-sent messages.
3. Confirm recipients: `gpualert config --show` and check `email.to_addresses`.
4. Read the combined log: every job's directory is printed by the CLI under "Log files written".
   The combined log includes timestamps for every line of stdout/stderr.

The job's exit code is independent of the notification. A non-zero `gpualert run` exit means the
*job* failed, not the email.

## Email arrived without log attachments

Possible causes:

- The job succeeded and `attach_logs_on_success = false` in your config. By design.
- A file became unreadable between job-end and email-send (rare). The notifier appends the
  unreadable path to the email body under `Skipped:` rather than failing the send.
- Attachment budget exceeded. Check the email body for an `artifacts_overflow.zip` mention or a
  `Skipped:` line — if logs were dropped you'll see them named.

## Job runs but `gpualert run` returns before it finishes

GPUAlert runs the command with `subprocess.Popen` and waits via `proc.wait()`. If the command
detaches itself (forks to background, daemonizes, runs `nohup ... &` internally) the wrapper sees
the parent exit and returns immediately. Wrap the *foreground* process, or use `gpualert slurm`
for jobs you submitted with `sbatch`.

## `gpualert slurm` says `sacct not found in PATH`

You're on a host without Slurm client tools installed. SSH into the login or submit node, or
load the Slurm module (`module load slurm`) if your site uses environment modules.

## Job killed by OOM but no error in email body

GPUAlert parses tracebacks and known patterns from the stdout/stderr *tail*. If the OOMKiller
killed your process before it produced any output, there will be nothing for `parse_errors` to
match — the email body will just show `Exit Code: -9` or similar. Check `dmesg | grep -i oom`
on the node for confirmation.

## Logs are huge and email bounced

The default per-file limit is 25 MB and the total budget is 45 MB. For very long-running jobs the
combined log can grow past 25 MB and get dropped entirely. Workarounds:

- Raise `artifacts.max_single_file_mb` in the config.
- Have your training script rotate or truncate its own stdout, so the wrapper writes less.
- Use `--no-notify` and then send the logs separately.

## Recovering logs from a completed job

```bash
gpualert logs              # most recent 10
gpualert logs --last 50    # last 50
```

Each row is a directory containing the three log files. They're plain text — open with `less`,
`cat`, `tail`, etc.

```bash
less ~/.gpualert/logs/20260524_213045_a1b2c3d4/combined.log
```

## Resetting state

```bash
gpualert config --reset    # delete config; will prompt for confirmation
rm -rf ~/.gpualert/logs    # delete all logs (manual)
```

Note: `--reset` only removes the config file, not the logs directory. Logs accumulate
indefinitely; clean them up yourself as needed.
