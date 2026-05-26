---
name: Bug report
about: Something is broken or behaves unexpectedly.
title: ""
labels: bug
assignees: ""
---

## What happened

Describe the actual behaviour you observed.

## What you expected

Describe what you expected to happen instead.

## Reproduction

The exact command you ran, with anything sensitive (passwords, recipient addresses)
redacted:

```bash
gpualert run -- ...
```

## Environment

- GPUAlert version: (run `gpualert version`)
- Python version:   (run `python --version`)
- OS:               (Linux distro + version, or macOS version)
- Slurm:            (yes/no; if yes, `sacct --version`)

## Logs

Paste the relevant lines from `~/.gpualert/logs/<job>/combined.log`. Redact any
secrets before pasting.

```
<paste here>
```

## Config (sanitized)

Output of `gpualert config --show`. The password is masked automatically, but
double-check before pasting.

```
<paste here>
```
