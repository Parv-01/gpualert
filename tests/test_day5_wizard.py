"""Day 5 tests — the config_manager wizard, including Gmail App Password hint."""
from __future__ import annotations


def test_gmail_hint_shown_for_gmail_username(tmp_path, monkeypatch):
    """Wizard must print the App Password URL when the user types a gmail address."""
    from gpualert.config import GPUAlertConfig, get_config_path
    from gpualert.config_manager import init_config_interactive

    # Redirect the config file to a tmp location so we don't clobber real config
    monkeypatch.setattr(
        "gpualert.config.get_config_path",
        lambda: tmp_path / "config.toml",
    )

    inputs = iter([
        "",                       # SMTP server (keep default)
        "",                       # port
        "parv@gmail.com",         # username -> triggers Gmail hint
        "to@example.com",         # to_addresses
    ])
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

    inputs = iter([
        "smtp.work.com",
        "465",
        "parv@work.com",          # NOT gmail
        "to@example.com",
    ])
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

    inputs = iter([
        "smtp.custom.com",
        "2525",
        "parv@custom.com",
        "a@example.com, b@example.com",
    ])
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
