"""
gpualert.config_manager — Interactive config wizard.

Kept separate from gpualert.config so importing the config module never
triggers input prompts (matters for tests, CI, anything non-interactive).
"""

from __future__ import annotations

import getpass
from typing import Callable, Optional

from gpualert.config import GPUAlertConfig, save_config

_GMAIL_APP_PWD_URL = "https://myaccount.google.com/apppasswords"

# parv signature for the input/output handles — lets tests inject stubs cleanly.
_PARV_DEFAULT_INPUT: Callable[[str], str] = input
_PARV_DEFAULT_GETPASS: Callable[[str], str] = getpass.getpass


def _prompt(
    label: str,
    current: str,
    secret: bool = False,
    input_fn: Optional[Callable[[str], str]] = None,
    getpass_fn: Optional[Callable[[str], str]] = None,
) -> str:
    """Single field prompt that keeps the existing value on empty input."""
    display = "***" if (secret and current) else current
    suffix = f" [{display}]: " if current else ": "
    use_input = input_fn or _PARV_DEFAULT_INPUT
    use_getpass = getpass_fn or _PARV_DEFAULT_GETPASS
    if secret:
        val = use_getpass(label + suffix) or current
    else:
        val = use_input(label + suffix) or current
    return val


def init_config_interactive(
    config: GPUAlertConfig,
    input_fn: Optional[Callable[[str], str]] = None,
    getpass_fn: Optional[Callable[[str], str]] = None,
    print_fn: Optional[Callable[[str], None]] = None,
) -> GPUAlertConfig:
    """Walk the user through SMTP setup, save, return the updated config."""
    out = print_fn or print

    out("")
    out("=== GPUAlert Configuration Wizard ===")
    out("Press Enter to keep current value (shown in brackets).")
    out("")

    # SMTP server prompt — loops until the answer looks like a hostname,
    # not an email address or an obvious typo. The previous wizard accepted
    # any string here, which let users type their email at the "SMTP server"
    # prompt by mistake and not notice for weeks.
    while True:
        candidate = _prompt(
            "SMTP server",
            config.smtp.server,
            input_fn=input_fn,
            getpass_fn=getpass_fn,
        ).strip()
        if not candidate:
            out("  SMTP server cannot be empty. Example: smtp.gmail.com")
            continue
        if "@" in candidate:
            out(
                f"  '{candidate}' looks like an email address, not a server. "
                "The SMTP server is a hostname like smtp.gmail.com. "
                "Your email goes in the next prompt (SMTP username)."
            )
            continue
        if "." not in candidate:
            out(
                f"  '{candidate}' does not look like a hostname "
                "(no dot). Did you mean smtp.gmail.com?"
            )
            continue
        if any(ch.isspace() for ch in candidate):
            out("  SMTP server cannot contain spaces.")
            continue
        config.smtp.server = candidate
        break
    port_str = _prompt(
        "SMTP port",
        str(config.smtp.port),
        input_fn=input_fn,
        getpass_fn=getpass_fn,
    )
    try:
        config.smtp.port = int(port_str) if port_str else 587
    except ValueError:
        config.smtp.port = 587

    config.smtp.username = _prompt(
        "SMTP username (your email)",
        config.smtp.username,
        input_fn=input_fn,
        getpass_fn=getpass_fn,
    )

    # Gmail App Password hint — fires when the username looks like Gmail.
    if config.smtp.username.lower().endswith("@gmail.com"):
        out("")
        out("Gmail detected. Use an App Password, not your regular password.")
        out(f"Generate one at: {_GMAIL_APP_PWD_URL}")
        out("(Requires 2FA enabled on the account.)")
        out("")

    config.smtp.password = _prompt(
        "SMTP password / App Password",
        config.smtp.password,
        secret=True,
        input_fn=input_fn,
        getpass_fn=getpass_fn,
    )
    config.email.from_address = config.smtp.username

    to_raw = _prompt(
        "Send notifications to (comma-separated)",
        ", ".join(config.email.to_addresses),
        input_fn=input_fn,
        getpass_fn=getpass_fn,
    )
    config.email.to_addresses = [e.strip() for e in to_raw.split(",") if e.strip()]

    save_config(config)
    return config
