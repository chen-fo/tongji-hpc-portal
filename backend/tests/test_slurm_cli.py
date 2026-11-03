import json
import sys

import pytest
from conftest import account_payload, migrate

from app import cli
from app.models.entities import OfficialAccount


def test_slurm_probe_requires_authorization_before_prompt_or_network(settings, monkeypatch):
    migrate(settings.database_url)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        sys, "argv", ["cli", "probe-slurm", "--account-id", "1", "--prompt-password"]
    )
    monkeypatch.setattr(
        cli.CredentialProvider, "prompt", lambda *_: pytest.fail("must not request a password")
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code != 0


def test_slurm_probe_reports_capabilities_without_storing_password(settings, monkeypatch, capsys):
    from app.ssh import slurm

    migrate(settings.database_url)
    engine = cli.make_engine(settings.database_url)
    with cli.make_session_factory(engine)() as db:
        db.add(OfficialAccount(**account_payload()))
        db.commit()
    engine.dispose()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "probe-slurm", "--account-id", "1", "--confirm-authorized", "--prompt-password"],
    )
    temporary_secret = "mock-password-not-for-logs"
    providers = []

    def prompt(provider, alias):
        provider._passwords[alias] = temporary_secret
        providers.append(provider)

    async def fake_probe(collector, account, since):
        assert account.alias == "official_a"
        assert collector.ssh.credentials._passwords[account.alias] == temporary_secret
        assert since.tzinfo is None
        return {"squeue_query_ok": True, "sacct_query_ok": True, "queue_count": 0}

    monkeypatch.setattr(cli.CredentialProvider, "prompt", prompt)
    monkeypatch.setattr(slurm, "probe_slurm", fake_probe)
    cli.main()
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "squeue_query_ok": True,
        "sacct_query_ok": True,
        "queue_count": 0,
    }
    assert temporary_secret not in output.out + output.err
    assert providers[0]._passwords == {}
