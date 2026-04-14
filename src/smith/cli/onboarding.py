from __future__ import annotations

import platform
from collections.abc import Callable
from pathlib import Path

from smith.config import (
    _RESERVED_REMOTE_NAMES,
    RemoteConfig,
    SmithConfig,
    _compute_api_url_for_remote,
    _default_config_path,
    save_config,
)

_PROVIDERS = ["github", "gitlab", "azdo", "youtrack"]

_PROVIDER_LABELS = {
    "github": "GitHub",
    "gitlab": "GitLab",
    "azdo": "Azure DevOps",
    "youtrack": "YouTrack",
}

_DEFAULT_HOSTS = {
    "github": "github.com",
    "gitlab": "gitlab.com",
    "azdo": "dev.azure.com",
    "youtrack": "",
}

_DEFAULT_TOKEN_ENVS = {
    "github": "GITHUB_TOKEN",
    "gitlab": "GITLAB_TOKEN",
    "youtrack": "YOUTRACK_TOKEN",
}

_EXAMPLE_CONFIG = """\
remotes:
  github-work:
    provider: github
    org: my-org
    token_env: GITHUB_TOKEN
    enabled: true
  gitlab-platform:
    provider: gitlab
    group: platform-team
    token_env: GITLAB_TOKEN
    enabled: true
"""


def _prompt_text(
    prompt: str,
    *,
    default: str = "",
    required: bool = False,
    validator: Callable[[str], str | None] | None = None,
) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"{prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(1)
        if not value:
            value = default
        if required and not value:
            print("  This field is required. Please enter a value.")
            continue
        if validator and value:
            error = validator(value)
            if error:
                print(f"  {error}")
                continue
        return value


def _prompt_choice(prompt: str, options: list[str], *, default: int = 1) -> int:
    for i, option in enumerate(options, 1):
        print(f"  {i}) {option}")
    while True:
        try:
            raw = input(f"{prompt} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(1)
        if not raw:
            return default
        try:
            choice = int(raw)
        except ValueError:
            print(f"  Enter a number between 1 and {len(options)}.")
            continue
        if 1 <= choice <= len(options):
            return choice
        print(f"  Enter a number between 1 and {len(options)}.")


def _prompt_yes_no(prompt: str, *, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"{prompt} [{hint}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(1)
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("  Please enter y or n.")


def _validate_remote_name(name: str) -> str | None:
    if name.lower() in _RESERVED_REMOTE_NAMES:
        reserved = ", ".join(sorted(_RESERVED_REMOTE_NAMES))
        return f"'{name}' is reserved. Avoid: {reserved}"
    if not name.replace("-", "").replace("_", "").isalnum():
        return "Name must contain only letters, numbers, hyphens, and underscores."
    return None


def _print_keychain_instructions(token_env: str) -> None:
    if platform.system() != "Darwin":
        return
    print()
    print("  Recommended: store your token securely in macOS Keychain:")
    print()
    print(f'    security add-generic-password -a "$USER" -s "{token_env}" -w "your-token-here"')
    print()
    print("  Then add to your shell profile (~/.zshrc):")
    print()
    print(f'    export {token_env}=$(security find-generic-password -a "$USER" -s "{token_env}" -w 2>/dev/null)')
    print()


def _print_azdo_auth_instructions() -> None:
    print()
    print("  Azure DevOps uses Azure CLI for authentication. Run:")
    print()
    print("    az login")
    print()


def _collect_remote(existing_names: set[str]) -> RemoteConfig:
    print()
    print("Select a provider:")
    provider_idx = _prompt_choice(
        "Provider",
        [_PROVIDER_LABELS[p] for p in _PROVIDERS],
    )
    provider = _PROVIDERS[provider_idx - 1]

    def _name_validator(name: str) -> str | None:
        error = _validate_remote_name(name)
        if error:
            return error
        if name in existing_names:
            return f"'{name}' is already configured. Choose a different name."
        return None

    default_name = provider if provider not in existing_names else ""
    name = _prompt_text(
        "Remote name",
        default=default_name,
        required=True,
        validator=_name_validator,
    )

    org = ""
    if provider in {"github", "azdo"}:
        org = _prompt_text("Organization", required=True)
    elif provider == "gitlab":
        org = _prompt_text("Group (optional, press Enter to skip)")

    default_host = _DEFAULT_HOSTS[provider]
    if provider == "youtrack":
        host = _prompt_text("Host (e.g. youtrack.example.com)", required=True)
    else:
        host = _prompt_text("Host", default=default_host) or default_host

    token_env: str | None = None
    if provider == "azdo":
        _print_azdo_auth_instructions()
    else:
        default_token = _DEFAULT_TOKEN_ENVS.get(provider, "")
        token_env = _prompt_text(
            "Token environment variable",
            default=default_token,
        ) or None
        if token_env:
            _print_keychain_instructions(token_env)

    api_url = _compute_api_url_for_remote(provider, host)

    return RemoteConfig(
        name=name,
        provider=provider,
        org=org,
        host=host,
        token_env=token_env,
        enabled=True,
        api_url=api_url,
    )


def _print_summary(config: SmithConfig, config_path: Path) -> None:
    print()
    print(f"Config saved to {config_path}")
    print()
    if not config.remotes:
        print("No remotes configured.")
        return
    print(f"Configured remotes ({len(config.remotes)}):")
    for remote in config.remotes.values():
        label = _PROVIDER_LABELS.get(remote.provider, remote.provider)
        org_info = f" org={remote.org}" if remote.org else ""
        print(f"  - {remote.name} ({label}{org_info}, host={remote.host})")
    print()
    print("Next steps:")
    print("  smith config list              # verify your remotes")
    print("  smith config show <remote>     # inspect a remote")
    first_remote = next(iter(config.remotes.values()), None)
    if first_remote and first_remote.provider != "youtrack":
        print(f"  smith {first_remote.name} repos          # list repositories")


def _print_manual_setup_instructions(config_path: Path) -> None:
    print()
    print(f"Config file created at {config_path}")
    print()
    print("Edit it to add your remotes. Example:")
    print()
    for line in _EXAMPLE_CONFIG.splitlines():
        print(f"  {line}")
    print()
    print("Then run `smith config list` to verify.")


def _print_remote_list(remotes: dict[str, RemoteConfig]) -> None:
    if not remotes:
        print("  (no remotes configured)")
        return
    for remote in remotes.values():
        label = _PROVIDER_LABELS.get(remote.provider, remote.provider)
        status = "enabled" if remote.enabled else "disabled"
        org_info = f" org={remote.org}" if remote.org else ""
        print(f"  - {remote.name} ({label}{org_info}, host={remote.host}, {status})")


def run_interactive_edit(config: SmithConfig, config_path: Path | None = None) -> SmithConfig:
    path = config_path or _default_config_path()
    remotes: dict[str, RemoteConfig] = dict(config.remotes)

    print()
    print("Current remotes:")
    _print_remote_list(remotes)

    while True:
        print()
        print("What would you like to do?")
        options = ["Add a new remote", "Edit an existing remote", "Remove a remote", "Done"]
        if not remotes:
            options = ["Add a new remote", "Done"]
        choice = _prompt_choice("Choice", options, default=len(options))

        if choice == len(options):
            break

        if choice == 1:
            remote = _collect_remote(existing_names=set(remotes.keys()))
            remotes[remote.name] = remote
            print(f"\n  Remote '{remote.name}' added.")
        elif choice == 2 and remotes:
            remote_names = list(remotes.keys())
            print()
            print("Select a remote to edit:")
            edit_idx = _prompt_choice("Remote", remote_names)
            old_name = remote_names[edit_idx - 1]
            names_without_current = set(remotes.keys()) - {old_name}
            del remotes[old_name]
            remote = _collect_remote(existing_names=names_without_current)
            remotes[remote.name] = remote
            print(f"\n  Remote '{remote.name}' updated.")
        elif choice == 3 and remotes:
            remote_names = list(remotes.keys())
            print()
            print("Select a remote to remove:")
            rm_idx = _prompt_choice("Remote", remote_names)
            rm_name = remote_names[rm_idx - 1]
            if _prompt_yes_no(f"Remove '{rm_name}'?", default=False):
                del remotes[rm_name]
                print(f"\n  Remote '{rm_name}' removed.")

    updated_config = SmithConfig(remotes=remotes, defaults=config.defaults)
    save_config(updated_config, config_path=path)
    _print_summary(updated_config, path)
    return updated_config


def run_interactive_init(config_path: Path | None = None) -> SmithConfig:
    path = config_path or _default_config_path()
    remotes: dict[str, RemoteConfig] = {}

    remote = _collect_remote(existing_names=set(remotes.keys()))
    remotes[remote.name] = remote

    while True:
        print()
        print("What would you like to do?")
        options = ["Add another remote", "Done"]
        if remotes:
            options = ["Add another remote", "Edit an existing remote", "Done"]
        choice = _prompt_choice("Choice", options, default=len(options))

        if choice == len(options):
            break
        elif choice == 1:
            remote = _collect_remote(existing_names=set(remotes.keys()))
            remotes[remote.name] = remote
        elif choice == 2 and len(options) == 3:
            remote_names = list(remotes.keys())
            print()
            print("Select a remote to edit:")
            edit_idx = _prompt_choice("Remote", remote_names)
            old_name = remote_names[edit_idx - 1]
            names_without_current = set(remotes.keys()) - {old_name}
            del remotes[old_name]
            remote = _collect_remote(existing_names=names_without_current)
            remotes[remote.name] = remote

    config = SmithConfig(remotes=remotes, defaults={})
    save_config(config, config_path=path)
    _print_summary(config, path)
    return config
