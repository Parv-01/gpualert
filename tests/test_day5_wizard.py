"""Day 5 tests — the config_manager wizard, including Gmail App Password hint."""

from __future__ import annotations


def test_gmail_hint_shown_for_gmail_username(tmp_path, monkeypatch):
    """Wizard must print the App Password URL when the user types a gmail address."""
    from gpualert.config import GPUAlertConfig
    from gpualert.config_manager import init_config_interactive

    # Redirect the config file to a tmp location so we don't clobber real config
    monkeypatch.setattr(
        "gpualert.config.get_config_path",
        lambda: tmp_path / "config.toml",
    )

    inputs = iter(
        [
            "",  # SMTP server (keep default)
            "",  # port
            "parv@gmail.com",  # username -> triggers Gmail hint
            "to@example.com",  # to_addresses
        ]
    )
    secrets = iter(["fakepass"])
    printed: list[str] = []

    cfg = GPUAlertConfig()
    init_config_interactive(
        cfg,
        input_fn=lambda _: next(inputs),
        getpass_fn=lambda _: next(secrets),
        print_fn=lambda s: printed.append(s),
    )

    joined = "\n".join(printed)
    assert "Gmail detected" in joined
    assert "myaccount.google.com/apppasswords" in joined


def test_no_gmail_hint_for_non_gmail(tmp_path, monkeypatch):
    """Non-Gmail addresses should NOT trigger the Gmail-specific hint."""
    from gpualert.config import GPUAlertConfig
    from gpualert.config_manager import init_config_interactive

    monkeypatch.setattr(
        "gpualert.config.get_config_path",
        lambda: tmp_path / "config.toml",
    )

    inputs = iter(
        [
            "smtp.work.com",
            "465",
            "parv@work.com",  # NOT gmail
            "to@example.com",
        ]
    )
    secrets = iter(["pw"])
    printed: list[str] = []

    init_config_interactive(
        GPUAlertConfig(),
        input_fn=lambda _: next(inputs),
        getpass_fn=lambda _: next(secrets),
        print_fn=lambda s: printed.append(s),
    )
    joined = "\n".join(printed)
    assert "Gmail" not in joined
    assert "apppasswords" not in joined


def test_wizard_persists_user_input(tmp_path, monkeypatch):
    """The values typed in should land on the returned config object."""
    from gpualert.config import GPUAlertConfig
    from gpualert.config_manager import init_config_interactive

    monkeypatch.setattr(
        "gpualert.config.get_config_path",
        lambda: tmp_path / "config.toml",
    )

    inputs = iter(
        [
            "smtp.custom.com",
            "2525",
            "parv@custom.com",
            "a@example.com, b@example.com",
        ]
    )
    secrets = iter(["sekret"])

    cfg = init_config_interactive(
        GPUAlertConfig(),
        input_fn=lambda _: next(inputs),
        getpass_fn=lambda _: next(secrets),
        print_fn=lambda _: None,
    )
    assert cfg.smtp.server == "smtp.custom.com"
    assert cfg.smtp.port == 2525
    assert cfg.smtp.username == "parv@custom.com"
    assert cfg.smtp.password == "sekret"
    assert cfg.email.from_address == "parv@custom.com"
    assert cfg.email.to_addresses == ["a@example.com", "b@example.com"]


def test_wizard_rejects_email_at_server_prompt():
    """Real-world bug: user typed their email at the SMTP-server prompt.
    The wizard must loop with a clear error until they give a hostname."""
    from gpualert.config import GPUAlertConfig
    from gpualert.config_manager import init_config_interactive

    cfg = GPUAlertConfig()
    answers = iter(
        [
            "parv@gmail.com",  # 1st try — email, rejected
            "localhost",  # 2nd try — no dot, rejected
            "smtp.gmail.com",  # 3rd try — valid hostname, accepted
            "587",  # port
            "parv@gmail.com",  # username
            "you@example.com",  # recipients
        ]
    )
    secrets = iter(["app-password-123"])
    errors = []
    init_config_interactive(
        cfg,
        input_fn=lambda _prompt: next(answers),
        getpass_fn=lambda _prompt: next(secrets),
        print_fn=lambda line: errors.append(line),
    )
    # We hit two validation errors before the third try landed.
    error_text = "\n".join(errors)
    assert "looks like an email address" in error_text
    assert "does not look like a hostname" in error_text
    assert cfg.smtp.server == "smtp.gmail.com"


def test_wizard_rejects_empty_and_whitespace_server():
    """Empty and whitespace-containing servers must also be rejected."""
    from gpualert.config import GPUAlertConfig
    from gpualert.config_manager import init_config_interactive

    cfg = GPUAlertConfig()
    cfg.smtp.server = ""  # start empty so the default doesn't auto-save
    answers = iter(
        [
            "",  # empty -> rejected
            "smtp .gmail.com",  # whitespace -> rejected
            "smtp.gmail.com",  # accepted
            "587",
            "parv@gmail.com",
            "you@example.com",
        ]
    )
    secrets = iter(["pw"])
    errors = []
    init_config_interactive(
        cfg,
        input_fn=lambda _prompt: next(answers),
        getpass_fn=lambda _prompt: next(secrets),
        print_fn=lambda line: errors.append(line),
    )
    error_text = "\n".join(errors)
    assert "cannot be empty" in error_text
    assert "cannot contain spaces" in error_text
    assert cfg.smtp.server == "smtp.gmail.com"
