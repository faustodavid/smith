from __future__ import annotations

import re
from collections.abc import Iterable
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from smith.auth import resolve_auth
from smith.cli.onboarding import (
    AuthScan,
    AuthScanStatus,
    _auth_prompt_choices,
    _collect_remote,
    _print_boot_screen,
    _print_panel,
    _prompt_choice,
    _prompt_text,
    _prompt_yes_no,
    _remote_auth_scan,
    _validate_remote_name,
    run_interactive_edit,
    run_interactive_init,
)
from smith.config import RemoteConfig, SmithConfig, load_config, save_config
from smith.secure_store import SecureStoreResult

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def stable_auth_scan():
    def missing_provider_scan() -> dict[str, AuthScan]:
        return {
            provider: AuthScan(AuthScanStatus.MISSING, "Auth can be added during setup")
            for provider in ("github", "gitlab", "azdo", "youtrack")
        }

    def missing_remote_scan(remotes: Iterable[RemoteConfig]) -> dict[str, AuthScan]:
        return {remote.name: AuthScan(AuthScanStatus.MISSING, "Auth can be added during setup") for remote in remotes}

    with (
        patch(
            "smith.cli.onboarding._scan_provider_auth",
            side_effect=missing_provider_scan,
        ),
        patch("smith.cli.onboarding._scan_remote_auth", side_effect=missing_remote_scan),
    ):
        yield


class TestValidateRemoteName:
    def test_reserved_name_rejected(self) -> None:
        assert _validate_remote_name("all") is not None
        assert _validate_remote_name("cache") is not None
        assert _validate_remote_name("config") is not None
        assert _validate_remote_name("skill") is not None

    def test_valid_name_accepted(self) -> None:
        assert _validate_remote_name("my-github") is None
        assert _validate_remote_name("work_gitlab") is None
        assert _validate_remote_name("azdo1") is None

    def test_invalid_characters_rejected(self) -> None:
        assert _validate_remote_name("my remote") is not None
        assert _validate_remote_name("remote!") is not None


class TestPromptText:
    def test_returns_user_input(self) -> None:
        with patch("builtins.input", return_value="hello"):
            assert _prompt_text("Enter") == "hello"

    def test_returns_default_on_empty(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _prompt_text("Enter", default="fallback") == "fallback"

    def test_required_retries_on_empty(self) -> None:
        inputs = iter(["", "", "value"])
        with patch("builtins.input", side_effect=inputs):
            assert _prompt_text("Enter", required=True) == "value"

    def test_validator_retries_on_error(self) -> None:
        inputs = iter(["bad", "good"])
        with patch("builtins.input", side_effect=inputs):
            result = _prompt_text(
                "Enter",
                required=True,
                validator=lambda v: "nope" if v == "bad" else None,
            )
            assert result == "good"

    def test_eof_exits(self) -> None:
        with patch("builtins.input", side_effect=EOFError):
            with pytest.raises(SystemExit):
                _prompt_text("Enter")


class TestPromptChoice:
    def test_returns_default_on_empty(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _prompt_choice("Pick", ["a", "b"], default=2) == 2

    def test_returns_selected(self) -> None:
        with patch("builtins.input", return_value="1"):
            assert _prompt_choice("Pick", ["a", "b"]) == 1

    def test_retries_on_invalid(self) -> None:
        inputs = iter(["0", "3", "abc", "2"])
        with patch("builtins.input", side_effect=inputs):
            assert _prompt_choice("Pick", ["a", "b"]) == 2


class TestPromptYesNo:
    def test_default_yes(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _prompt_yes_no("Continue?") is True

    def test_default_no(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _prompt_yes_no("Continue?", default=False) is False

    def test_yes_inputs(self) -> None:
        for value in ["y", "yes", "Y", "YES"]:
            with patch("builtins.input", return_value=value):
                assert _prompt_yes_no("Continue?", default=False) is True

    def test_no_inputs(self) -> None:
        for value in ["n", "no", "N", "NO"]:
            with patch("builtins.input", return_value=value):
                assert _prompt_yes_no("Continue?") is False


class TestRendering:
    def test_plain_boot_screen_uses_direct_step_copy(self, tmp_path: Path) -> None:
        output = StringIO()
        with (
            patch("sys.stdout", output),
            patch("smith.cli.onboarding._ansi_enabled", return_value=False),
            patch("smith.cli.terminal_ui._ansi_enabled", return_value=False),
        ):
            _print_boot_screen(tmp_path / "config.yaml")

        text = output.getvalue()
        assert "Step 1 of 5: Wake the console" in text
        assert "MISSION" not in text
        assert "+---" not in text

    def test_panel_wraps_long_words_inside_border(self) -> None:
        output = StringIO()
        long_path = "/tmp/" + ("verylongpathsegment" * 8) + "/config.yaml"
        with (
            patch("sys.stdout", output),
            patch("smith.cli.terminal_ui._ansi_enabled", return_value=True),
            patch("smith.cli.terminal_ui._terminal_width", return_value=40),
        ):
            _print_panel("TEST", [f"Saved: {long_path}"])

        for line in output.getvalue().splitlines():
            assert len(_ANSI_RE.sub("", line)) <= 40


class TestAuthReadiness:
    def test_self_hosted_gitlab_uses_host_scoped_runtime_token_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setattr("smith.credentials.get_stored_token", lambda token_env: None)
        calls: list[list[str]] = []

        def _fake_run(args: list[str], **kwargs: object) -> object:
            calls.append(args)
            return SimpleNamespace(stdout="self-hosted-token\n")

        monkeypatch.setattr("smith.credentials.subprocess.run", _fake_run)
        remote = RemoteConfig(
            name="self-hosted",
            provider="gitlab",
            org="platform",
            host="gitlab.example.test",
            token_env=None,
            enabled=True,
            api_url="https://gitlab.example.test/api/v4",
        )

        scan = _remote_auth_scan(remote)

        assert scan.status is AuthScanStatus.OK
        assert scan.message == "GitLab: `glab config get token --host gitlab.example.test` returned a token"
        assert calls == [["glab", "config", "get", "token", "--host", "gitlab.example.test"]]


class TestAuthPromptChoices:
    def test_public_github_default_runtime_choice_does_not_duplicate_persisted_env_choice(self) -> None:
        choices = _auth_prompt_choices(resolve_auth("github", api_url="https://api.github.com"))
        labels = [choice.label for choice in choices]

        assert labels[0] == "Use GITHUB_TOKEN env/secure-store or gh auth login at runtime"
        assert labels.count("Use GITHUB_TOKEN env/secure-store or gh auth login at runtime") == 1
        assert "Persist GITHUB_TOKEN as this remote's token_env" not in labels
        assert "Paste token into secure store as GITHUB_TOKEN for runtime auth" in labels

    def test_public_gitlab_default_runtime_choice_does_not_duplicate_persisted_env_choice(self) -> None:
        choices = _auth_prompt_choices(resolve_auth("gitlab", api_url="https://gitlab.com/api/v4"))
        labels = [choice.label for choice in choices]

        assert labels[0] == "Use GITLAB_TOKEN env/secure-store or glab auth login at runtime"
        assert labels.count("Use GITLAB_TOKEN env/secure-store or glab auth login at runtime") == 1
        assert "Persist GITLAB_TOKEN as this remote's token_env" not in labels
        assert "Paste token into secure store as GITLAB_TOKEN for runtime auth" in labels

    def test_youtrack_default_runtime_choice_does_not_duplicate_persisted_env_choice(self) -> None:
        choices = _auth_prompt_choices(resolve_auth("youtrack"))
        labels = [choice.label for choice in choices]

        assert labels[0] == "Use YOUTRACK_TOKEN env/secure-store at runtime"
        assert labels.count("Use YOUTRACK_TOKEN env/secure-store at runtime") == 1
        assert "Persist YOUTRACK_TOKEN as this remote's token_env" not in labels
        assert "Paste token into secure store as YOUTRACK_TOKEN for runtime auth" in labels

    def test_github_enterprise_does_not_offer_generic_github_token_default(self) -> None:
        choices = _auth_prompt_choices(resolve_auth("github", api_url="https://ghe.example.test/api/v3"))
        labels = [choice.label for choice in choices]

        assert labels[0] == "Use gh auth login --hostname ghe.example.test at runtime"
        assert all("GITHUB_TOKEN" not in label for label in labels)

    def test_azdo_default_runtime_auth_is_azure_cli_with_explicit_pat_choice(self) -> None:
        choices = _auth_prompt_choices(resolve_auth("azdo"))
        labels = [choice.label for choice in choices]

        assert labels[0] == "Use az login at runtime"
        assert "Persist AZURE_DEVOPS_PAT as this remote's token_env" in labels
        assert "Paste PAT into secure store as AZURE_DEVOPS_PAT and persist token_env" in labels


class TestCollectRemote:
    def test_github_remote(self) -> None:
        inputs = iter(
            [
                "1",  # provider: GitHub
                "my-gh",  # name
                "octo-org",  # org
                "",  # host: default github.com
                "",  # auth: gh/default auth
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.name == "my-gh"
        assert remote.provider == "github"
        assert remote.org == "octo-org"
        assert remote.host == "github.com"
        assert remote.token_env is None
        assert remote.enabled is True

    def test_gitlab_remote_with_group(self) -> None:
        inputs = iter(
            [
                "2",  # provider: GitLab
                "my-gl",  # name
                "platform-team",  # group
                "",  # host: default gitlab.com
                "",  # auth: glab/default auth
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.provider == "gitlab"
        assert remote.org == "platform-team"
        assert remote.host == "gitlab.com"
        assert remote.token_env is None

    def test_azdo_remote(self) -> None:
        inputs = iter(
            [
                "3",  # provider: Azure DevOps
                "my-azdo",  # name
                "acme-corp",  # org
                "",  # host: default dev.azure.com
                "",  # auth: Azure CLI
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.provider == "azdo"
        assert remote.org == "acme-corp"
        assert remote.host == "dev.azure.com"
        assert remote.token_env is None

    def test_github_remote_can_store_default_runtime_token_without_persisting_token_env(self) -> None:
        inputs = iter(
            [
                "1",  # provider: GitHub
                "my-gh",  # name
                "octo-org",  # org
                "",  # host
                "2",  # auth: store default runtime token
            ]
        )
        with (
            patch("builtins.input", side_effect=inputs),
            patch("getpass.getpass", return_value="secret-token"),
            patch(
                "smith.cli.onboarding.store_token",
                return_value=SecureStoreResult(
                    ok=True,
                    token_env="GITHUB_TOKEN",
                    message="Stored GITHUB_TOKEN in the OS secure credential store.",
                ),
            ),
        ):
            remote = _collect_remote(existing_names=set())
        assert remote.token_env is None

    def test_custom_token_env_rejects_token_like_value(self) -> None:
        inputs = iter(
            [
                "1",  # provider: GitHub
                "my-gh",  # name
                "octo-org",  # org
                "",  # host
                "3",  # custom token env
                "ghp_secretvalue",  # invalid: looks like a pasted token, not uppercase env var
                "TEAM_GITHUB_TOKEN",  # valid
                "n",  # do not store now
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.token_env == "TEAM_GITHUB_TOKEN"

    def test_secure_store_token_prompt_never_prints_token(self) -> None:
        inputs = iter(
            [
                "1",  # provider: GitHub
                "my-gh",  # name
                "octo-org",  # org
                "",  # host
                "2",  # paste token into secure store
            ]
        )
        output = StringIO()
        with (
            patch("builtins.input", side_effect=inputs),
            patch("getpass.getpass", return_value="secret-token"),
            patch(
                "smith.cli.onboarding.store_token",
                return_value=SecureStoreResult(
                    ok=True,
                    token_env="GITHUB_TOKEN",
                    message="Stored GITHUB_TOKEN in the OS secure credential store.",
                ),
            ),
            patch("sys.stdout", output),
        ):
            remote = _collect_remote(existing_names=set())

        assert remote.token_env is None
        assert "secret-token" not in output.getvalue()

    def test_youtrack_remote(self) -> None:
        inputs = iter(
            [
                "4",  # provider: YouTrack
                "my-yt",  # name
                "youtrack.example.com",  # host (required)
                "",  # auth: runtime YOUTRACK_TOKEN
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.provider == "youtrack"
        assert remote.host == "youtrack.example.com"
        assert remote.token_env is None

    def test_youtrack_remote_can_store_default_runtime_token_without_persisting_token_env(self) -> None:
        inputs = iter(
            [
                "4",  # provider: YouTrack
                "my-yt",  # name
                "youtrack.example.com",  # host
                "2",  # store YOUTRACK_TOKEN for runtime auth
            ]
        )
        with (
            patch("builtins.input", side_effect=inputs),
            patch("getpass.getpass", return_value="secret-token"),
            patch(
                "smith.cli.onboarding.store_token",
                return_value=SecureStoreResult(
                    ok=True,
                    token_env="YOUTRACK_TOKEN",
                    message="Stored YOUTRACK_TOKEN in the OS secure credential store.",
                ),
            ),
        ):
            remote = _collect_remote(existing_names=set())
        assert remote.provider == "youtrack"
        assert remote.token_env is None

    def test_youtrack_remote_can_skip_auth(self) -> None:
        inputs = iter(
            [
                "4",  # provider: YouTrack
                "my-yt",  # name
                "youtrack.example.com",  # host
                "4",  # skip auth
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.provider == "youtrack"
        assert remote.token_env is None

    def test_duplicate_name_retries(self) -> None:
        inputs = iter(
            [
                "1",  # provider: GitHub
                "existing",  # name: already taken
                "new-name",  # retry with valid name
                "my-org",  # org
                "",  # host
                "",  # auth: gh/default auth
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names={"existing"})
        assert remote.name == "new-name"

    def test_reserved_name_retries(self) -> None:
        inputs = iter(
            [
                "1",  # provider: GitHub
                "all",  # reserved name
                "my-gh",  # retry
                "org",  # org
                "",  # host
                "",  # auth: gh/default auth
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.name == "my-gh"


class TestRunInteractiveInit:
    def test_single_remote_then_done(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        inputs = iter(
            [
                "1",  # provider: GitHub
                "",  # name: default github
                "my-org",  # org
                "",  # host
                "",  # auth: gh/default auth
                "3",  # done (3rd option since remotes exist)
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            config = run_interactive_init(config_path=config_path)

        assert config_path.exists()
        assert "github" in config.remotes
        assert config.remotes["github"].org == "my-org"

        loaded = load_config(config_path=config_path)
        assert "github" in loaded.remotes

    def test_two_remotes(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        inputs = iter(
            [
                "1",  # provider: GitHub
                "gh",  # name
                "org1",  # org
                "",  # host
                "",  # auth: gh/default auth
                "1",  # add another
                "2",  # provider: GitLab
                "gl",  # name
                "team",  # group
                "",  # host
                "",  # auth: glab/default auth
                "3",  # done
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            config = run_interactive_init(config_path=config_path)

        assert len(config.remotes) == 2
        assert "gh" in config.remotes
        assert "gl" in config.remotes

    def test_edit_existing_remote(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        inputs = iter(
            [
                "1",  # provider: GitHub
                "gh",  # name
                "old-org",  # org
                "",  # host
                "",  # auth: gh/default auth
                "2",  # edit existing
                "1",  # select "gh"
                "1",  # provider: GitHub
                "gh",  # name (same)
                "new-org",  # org (changed)
                "",  # host
                "",  # auth: gh/default auth
                "3",  # done
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            config = run_interactive_init(config_path=config_path)

        assert config.remotes["gh"].org == "new-org"


def _make_config_with_remote(
    tmp_path: Path,
    *,
    name: str = "gh",
    provider: str = "github",
    org: str = "my-org",
    host: str = "github.com",
    token_env: str | None = "GITHUB_TOKEN",
) -> tuple[Path, SmithConfig]:
    config_path = tmp_path / "config.yaml"
    remote = RemoteConfig(
        name=name,
        provider=provider,
        org=org,
        host=host,
        token_env=token_env,
        enabled=True,
        api_url="https://api.github.com",
    )
    config = SmithConfig(remotes={name: remote}, defaults={})
    save_config(config, config_path=config_path)
    return config_path, config


class TestRunInteractiveEdit:
    def test_add_remote_then_done(self, tmp_path: Path) -> None:
        config_path, config = _make_config_with_remote(tmp_path)
        inputs = iter(
            [
                "1",  # add a new remote
                "2",  # provider: GitLab
                "gl",  # name
                "team",  # group
                "",  # host
                "",  # auth: glab/default auth
                "4",  # done
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert len(updated.remotes) == 2
        assert "gh" in updated.remotes
        assert "gl" in updated.remotes

    def test_edit_existing_remote(self, tmp_path: Path) -> None:
        config_path, config = _make_config_with_remote(tmp_path)
        inputs = iter(
            [
                "2",  # edit existing
                "1",  # select "gh"
                "1",  # provider: GitHub
                "gh",  # same name
                "new-org",  # changed org
                "",  # host
                "",  # auth: gh/default auth
                "4",  # done
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert updated.remotes["gh"].org == "new-org"

    def test_remove_remote(self, tmp_path: Path) -> None:
        config_path, config = _make_config_with_remote(tmp_path)
        inputs = iter(
            [
                "3",  # remove a remote
                "1",  # select "gh"
                "y",  # confirm removal
                "2",  # done (now only 2 options: add, done)
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert len(updated.remotes) == 0

    def test_remove_remote_cancelled(self, tmp_path: Path) -> None:
        config_path, config = _make_config_with_remote(tmp_path)
        inputs = iter(
            [
                "3",  # remove a remote
                "1",  # select "gh"
                "n",  # cancel removal
                "4",  # done
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert "gh" in updated.remotes

    def test_done_immediately(self, tmp_path: Path) -> None:
        config_path, config = _make_config_with_remote(tmp_path)
        inputs = iter(
            [
                "4",  # done
            ]
        )
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert updated.remotes == config.remotes

    def test_preserves_defaults(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        remote = RemoteConfig(
            name="gh",
            provider="github",
            org="org",
            host="github.com",
            token_env="GITHUB_TOKEN",
            enabled=True,
            api_url="https://api.github.com",
        )
        config = SmithConfig(remotes={"gh": remote}, defaults={"some_key": "some_value"})
        save_config(config, config_path=config_path)
        inputs = iter(["4"])  # done immediately
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)
        assert updated.defaults == {"some_key": "some_value"}
