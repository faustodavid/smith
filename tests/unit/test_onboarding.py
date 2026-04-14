from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from smith.cli.onboarding import (
    _collect_remote,
    _print_keychain_instructions,
    _prompt_choice,
    _prompt_text,
    _prompt_yes_no,
    _validate_remote_name,
    run_interactive_edit,
    run_interactive_init,
)
from smith.config import RemoteConfig, SmithConfig, load_config, save_config


class TestValidateRemoteName:
    def test_reserved_name_rejected(self) -> None:
        assert _validate_remote_name("all") is not None
        assert _validate_remote_name("cache") is not None
        assert _validate_remote_name("config") is not None

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


class TestCollectRemote:
    def test_github_remote(self) -> None:
        inputs = iter([
            "1",         # provider: GitHub
            "my-gh",     # name
            "octo-org",  # org
            "",          # host: default github.com
            "",          # token_env: default GITHUB_TOKEN
        ])
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.name == "my-gh"
        assert remote.provider == "github"
        assert remote.org == "octo-org"
        assert remote.host == "github.com"
        assert remote.token_env == "GITHUB_TOKEN"
        assert remote.enabled is True

    def test_gitlab_remote_with_group(self) -> None:
        inputs = iter([
            "2",              # provider: GitLab
            "my-gl",          # name
            "platform-team",  # group
            "",               # host: default gitlab.com
            "",               # token_env: default GITLAB_TOKEN
        ])
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.provider == "gitlab"
        assert remote.org == "platform-team"
        assert remote.host == "gitlab.com"
        assert remote.token_env == "GITLAB_TOKEN"

    def test_azdo_remote(self) -> None:
        inputs = iter([
            "3",         # provider: Azure DevOps
            "my-azdo",   # name
            "acme-corp", # org
            "",          # host: default dev.azure.com
        ])
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.provider == "azdo"
        assert remote.org == "acme-corp"
        assert remote.host == "dev.azure.com"
        assert remote.token_env is None

    def test_youtrack_remote(self) -> None:
        inputs = iter([
            "4",                      # provider: YouTrack
            "my-yt",                  # name
            "youtrack.example.com",   # host (required)
            "",                       # token_env: default YOUTRACK_TOKEN
        ])
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.provider == "youtrack"
        assert remote.host == "youtrack.example.com"
        assert remote.token_env == "YOUTRACK_TOKEN"

    def test_duplicate_name_retries(self) -> None:
        inputs = iter([
            "1",         # provider: GitHub
            "existing",  # name: already taken
            "new-name",  # retry with valid name
            "my-org",    # org
            "",          # host
            "",          # token_env
        ])
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names={"existing"})
        assert remote.name == "new-name"

    def test_reserved_name_retries(self) -> None:
        inputs = iter([
            "1",         # provider: GitHub
            "all",       # reserved name
            "my-gh",     # retry
            "org",       # org
            "",          # host
            "",          # token_env
        ])
        with patch("builtins.input", side_effect=inputs):
            remote = _collect_remote(existing_names=set())
        assert remote.name == "my-gh"


class TestRunInteractiveInit:
    def test_single_remote_then_done(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        inputs = iter([
            "1",         # provider: GitHub
            "",          # name: default github
            "my-org",    # org
            "",          # host
            "",          # token_env
            "3",         # done (3rd option since remotes exist)
        ])
        with patch("builtins.input", side_effect=inputs):
            config = run_interactive_init(config_path=config_path)

        assert config_path.exists()
        assert "github" in config.remotes
        assert config.remotes["github"].org == "my-org"

        loaded = load_config(config_path=config_path)
        assert "github" in loaded.remotes

    def test_two_remotes(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        inputs = iter([
            "1",             # provider: GitHub
            "gh",            # name
            "org1",          # org
            "",              # host
            "",              # token_env
            "1",             # add another
            "2",             # provider: GitLab
            "gl",            # name
            "team",          # group
            "",              # host
            "",              # token_env
            "3",             # done
        ])
        with patch("builtins.input", side_effect=inputs):
            config = run_interactive_init(config_path=config_path)

        assert len(config.remotes) == 2
        assert "gh" in config.remotes
        assert "gl" in config.remotes

    def test_edit_existing_remote(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        inputs = iter([
            "1",             # provider: GitHub
            "gh",            # name
            "old-org",       # org
            "",              # host
            "",              # token_env
            "2",             # edit existing
            "1",             # select "gh"
            "1",             # provider: GitHub
            "gh",            # name (same)
            "new-org",       # org (changed)
            "",              # host
            "",              # token_env
            "3",             # done
        ])
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
        inputs = iter([
            "1",             # add a new remote
            "2",             # provider: GitLab
            "gl",            # name
            "team",          # group
            "",              # host
            "",              # token_env
            "4",             # done
        ])
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert len(updated.remotes) == 2
        assert "gh" in updated.remotes
        assert "gl" in updated.remotes

    def test_edit_existing_remote(self, tmp_path: Path) -> None:
        config_path, config = _make_config_with_remote(tmp_path)
        inputs = iter([
            "2",             # edit existing
            "1",             # select "gh"
            "1",             # provider: GitHub
            "gh",            # same name
            "new-org",       # changed org
            "",              # host
            "",              # token_env
            "4",             # done
        ])
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert updated.remotes["gh"].org == "new-org"

    def test_remove_remote(self, tmp_path: Path) -> None:
        config_path, config = _make_config_with_remote(tmp_path)
        inputs = iter([
            "3",             # remove a remote
            "1",             # select "gh"
            "y",             # confirm removal
            "2",             # done (now only 2 options: add, done)
        ])
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert len(updated.remotes) == 0

    def test_remove_remote_cancelled(self, tmp_path: Path) -> None:
        config_path, config = _make_config_with_remote(tmp_path)
        inputs = iter([
            "3",             # remove a remote
            "1",             # select "gh"
            "n",             # cancel removal
            "4",             # done
        ])
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert "gh" in updated.remotes

    def test_done_immediately(self, tmp_path: Path) -> None:
        config_path, config = _make_config_with_remote(tmp_path)
        inputs = iter([
            "4",             # done
        ])
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)

        assert updated.remotes == config.remotes

    def test_preserves_defaults(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        remote = RemoteConfig(
            name="gh", provider="github", org="org", host="github.com",
            token_env="GITHUB_TOKEN", enabled=True, api_url="https://api.github.com",
        )
        config = SmithConfig(remotes={"gh": remote}, defaults={"some_key": "some_value"})
        save_config(config, config_path=config_path)
        inputs = iter(["4"])  # done immediately
        with patch("builtins.input", side_effect=inputs):
            updated = run_interactive_edit(config, config_path=config_path)
        assert updated.defaults == {"some_key": "some_value"}


class TestPrintKeychainInstructions:
    def test_prints_on_darwin(self) -> None:
        output = StringIO()
        with patch("sys.stdout", output), patch("smith.cli.onboarding.platform.system", return_value="Darwin"):
            _print_keychain_instructions("GITHUB_TOKEN")
        text = output.getvalue()
        assert "security add-generic-password" in text
        assert "GITHUB_TOKEN" in text

    def test_skips_on_linux(self) -> None:
        output = StringIO()
        with patch("sys.stdout", output), patch("smith.cli.onboarding.platform.system", return_value="Linux"):
            _print_keychain_instructions("GITHUB_TOKEN")
        assert output.getvalue() == ""
