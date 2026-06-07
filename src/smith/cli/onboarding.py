from __future__ import annotations

import getpass
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from smith.auth import (
    PROVIDER_AUTH_ORDER,
    ResolvedAuth,
    normalize_token_env,
    provider_auth_policy,
    resolve_auth,
)
from smith.cli.terminal_ui import (
    _accent,
    _ansi_enabled,
    _dim,
    _print_panel,
    _print_rule,
    _print_wrapped,
    _status,
)
from smith.config import (
    _NEW_REMOTE_RESERVED_NAMES,
    RemoteConfig,
    SmithConfig,
    _compute_api_url_for_remote,
    _default_config_path,
    save_config,
)
from smith.credentials import (
    AuthScan,
    AuthScanStatus,
    auth_readiness_scan,
    runtime_auth_message,
    runtime_source_label,
    token_source_label,
)
from smith.secure_store import store_token

_PROVIDERS = list(PROVIDER_AUTH_ORDER)

_PROVIDER_BADGES = {
    "github": "GH",
    "gitlab": "GL",
    "azdo": "AZ",
    "youtrack": "YT",
}

_PROVIDER_TAGLINES = {
    "github": "repos, PRs, Actions, issues",
    "gitlab": "groups, merge requests, pipelines",
    "azdo": "projects, repos, builds, work items",
    "youtrack": "issues, searches, story context",
}

_STAGES = ["Boot", "Provider", "Remote", "Auth", "Ready"]


class AuthPromptChoiceKind(StrEnum):
    RUNTIME = "runtime"
    DEFAULT_ENV = "default_env"
    STORE_DEFAULT = "store_default"
    CUSTOM_ENV = "custom_env"
    SKIP = "skip"


@dataclass(frozen=True)
class AuthPromptChoice:
    kind: AuthPromptChoiceKind
    label: str
    token_env_to_persist: str | None = None
    token_env_to_store: str | None = None


def _provider_label(provider: str) -> str:
    return provider_auth_policy(provider).label


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
        print(f"  {_accent(str(i))}) {option}")
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
    if name.lower() in _NEW_REMOTE_RESERVED_NAMES:
        reserved = ", ".join(sorted(_NEW_REMOTE_RESERVED_NAMES))
        return f"'{name}' is reserved. Avoid: {reserved}"
    if not name.replace("-", "").replace("_", "").isalnum():
        return "Name must contain only letters, numbers, hyphens, and underscores."
    return None


def _print_progress(step: int, total: int, label: str) -> None:
    if not _ansi_enabled():
        print()
        print(f"Step {step} of {total}: {label}")
        return

    rail = []
    for index, stage in enumerate(_STAGES, 1):
        if index < step:
            marker = _status("OK")
        elif index == step:
            marker = _accent(">>")
        else:
            marker = ".."
        rail.append(f"{marker} {stage}")
    print()
    print(f"  {_dim(' -> '.join(rail))}")
    print(f"  {_accent(f'MISSION {step}/{total}')} {label}")


def _provider_menu_options(auth_scan: Mapping[str, AuthScan]) -> list[str]:
    options = []
    for provider in _PROVIDERS:
        scan = auth_scan[provider]
        status = _render_auth_status(scan.status)
        label = _provider_label(provider)
        if _ansi_enabled():
            options.append(f"{label:<15} {_PROVIDER_BADGES[provider]:<2} {status:<9} {_PROVIDER_TAGLINES[provider]}")
        else:
            options.append(f"{label} - {status} - {_PROVIDER_TAGLINES[provider]}")
    return options


def _remote_call_sign(remote: RemoteConfig) -> str:
    badge = _PROVIDER_BADGES.get(remote.provider, remote.provider[:2].upper())
    return f"{badge}:{remote.name}"


def _render_auth_status(status: AuthScanStatus) -> str:
    return _status(status.value)


def _auth_scan_line(provider: str) -> AuthScan:
    return auth_readiness_scan(resolve_auth(provider))


def _scan_provider_auth() -> dict[str, AuthScan]:
    with ThreadPoolExecutor(max_workers=len(_PROVIDERS)) as executor:
        return dict(zip(_PROVIDERS, executor.map(_auth_scan_line, _PROVIDERS), strict=True))


def _remote_auth_scan(remote: RemoteConfig) -> AuthScan:
    return auth_readiness_scan(resolve_auth(remote.provider, token_env=remote.token_env, api_url=remote.api_url))


def _scan_remote_auth(remotes: Iterable[RemoteConfig]) -> dict[str, AuthScan]:
    remote_list = list(remotes)
    if not remote_list:
        return {}
    with ThreadPoolExecutor(max_workers=min(len(remote_list), 8)) as executor:
        scans = executor.map(_remote_auth_scan, remote_list)
        return {remote.name: scan for remote, scan in zip(remote_list, scans, strict=True)}


def _print_boot_screen(config_path: Path, auth_scan: Mapping[str, AuthScan] | None = None) -> None:
    scan = auth_scan or _scan_provider_auth()
    ready_count = sum(1 for item in scan.values() if item.status is AuthScanStatus.OK)
    missing_count = len(scan) - ready_count
    print()
    _print_panel(
        "SMITH REMOTE QUEST" if _ansi_enabled() else "Smith Remote Setup",
        [
            "Objective: connect the places your agent can investigate.",
            f"Save slot: {config_path}",
            f"Signal scan: {ready_count} ready, {missing_count} need setup.",
            "Secret rule: config stores remote details and token env var names only.",
        ],
    )
    _print_progress(1, len(_STAGES), "Wake the console")
    print()
    print(f"  {_accent('Signal sweep')}")
    for provider in _PROVIDERS:
        item = scan[provider]
        print(f"    {_render_auth_status(item.status):<9} {item.message}")
    print()


def _validate_token_env_name(value: str) -> str | None:
    try:
        normalize_token_env(value)
    except ValueError:
        return "Use an uppercase env var name like GITHUB_TOKEN or TEAM_GITLAB_TOKEN, not a token value."
    return None


def _prompt_token_env(default: str | None = None) -> str:
    return _prompt_text(
        "Token environment variable",
        default=default or "",
        required=True,
        validator=_validate_token_env_name,
    )


def _store_token_interactively(provider: str, token_env: str) -> None:
    policy = provider_auth_policy(provider)
    print()
    title = f"SECRET VAULT // {policy.label}" if _ansi_enabled() else f"Token Setup - {policy.label}"
    _print_rule(title)
    _print_wrapped(
        f"Paste a {policy.token_hint}. It will be masked, stored with the OS secure credential backend, and never written to config."
    )
    print()
    token = getpass.getpass(f"Paste token for {token_env}: ").strip()
    result = store_token(token_env, token)
    if result.ok:
        print(f"  {_status('OK')} {result.message}")
        return
    print(f"  {_status('NEEDS')} {result.message}")
    print(f"  {_dim(f'Set {token_env} in your environment before running provider commands.')}")


def _auth_prompt_choices(auth: ResolvedAuth) -> tuple[AuthPromptChoice, ...]:
    policy = provider_auth_policy(auth.provider)
    choices: list[AuthPromptChoice] = []
    if auth.implicit_token_env or auth.cli_login_command or auth.cli_status_command:
        choices.append(AuthPromptChoice(AuthPromptChoiceKind.RUNTIME, f"Use {runtime_source_label(auth)} at runtime"))
    token_env_to_persist = auth.default_persist_token_env
    token_env_to_store = auth.default_store_token_env
    if token_env_to_persist:
        choices.extend(
            [
                AuthPromptChoice(
                    AuthPromptChoiceKind.DEFAULT_ENV,
                    f"Persist {token_env_to_persist} as this remote's token_env",
                    token_env_to_persist,
                ),
            ]
        )
    if token_env_to_store:
        store_label = f"Paste {policy.token_kind} into secure store as {token_env_to_store}"
        if token_env_to_persist:
            store_label = f"{store_label} and persist token_env"
        else:
            store_label = f"{store_label} for runtime auth"
        choices.extend(
            [
                AuthPromptChoice(
                    AuthPromptChoiceKind.STORE_DEFAULT,
                    store_label,
                    token_env_to_persist,
                    token_env_to_store,
                ),
            ]
        )
    choices.extend(
        [
            AuthPromptChoice(AuthPromptChoiceKind.CUSTOM_ENV, f"Use a custom {policy.token_kind} env var"),
            AuthPromptChoice(AuthPromptChoiceKind.SKIP, "Skip auth for now"),
        ]
    )
    return tuple(choices)


def _custom_token_env_default(auth: ResolvedAuth) -> str | None:
    return auth.default_persist_token_env


def _collect_token_source(auth: ResolvedAuth) -> str | None:
    policy = provider_auth_policy(auth.provider)
    print()
    _print_progress(4, len(_STAGES), "Choose the auth loadout")
    _print_rule(f"LOADOUT // {policy.label}" if _ansi_enabled() else f"Auth - {policy.label}")

    choices = _auth_prompt_choices(auth)
    selected = choices[_prompt_choice("Auth method", [choice.label for choice in choices], default=1) - 1]
    if selected.kind is AuthPromptChoiceKind.RUNTIME:
        print(f"  {_status('SAFE')} {runtime_auth_message(auth)}")
        return None
    if selected.kind is AuthPromptChoiceKind.DEFAULT_ENV:
        token_env = selected.token_env_to_persist
        if token_env is None:
            raise RuntimeError("Auth choice missing token_env_to_persist")
        print(f"  {_status('SAFE')} Config will reference {token_env}; Smith will also check secure storage.")
        return token_env
    if selected.kind is AuthPromptChoiceKind.STORE_DEFAULT:
        token_env_to_store = selected.token_env_to_store
        if token_env_to_store is None:
            raise RuntimeError("Auth choice missing token_env_to_store")
        _store_token_interactively(auth.provider, token_env_to_store)
        return selected.token_env_to_persist
    if selected.kind is AuthPromptChoiceKind.CUSTOM_ENV:
        token_env = _prompt_token_env(_custom_token_env_default(auth))
        if _prompt_yes_no(f"Store a {policy.token_kind} for this env var now?", default=False):
            _store_token_interactively(auth.provider, token_env)
        return token_env
    return None


def _collect_remote(existing_names: set[str], provider_auth_scan: Mapping[str, AuthScan] | None = None) -> RemoteConfig:
    print()
    _print_progress(2, len(_STAGES), "Scout a remote")
    _print_rule("MAP ROOM // PROVIDER" if _ansi_enabled() else "Provider")
    auth_scan = provider_auth_scan or _scan_provider_auth()
    provider_idx = _prompt_choice(
        "Provider",
        _provider_menu_options(auth_scan),
    )
    provider = _PROVIDERS[provider_idx - 1]
    policy = provider_auth_policy(provider)
    _print_progress(3, len(_STAGES), f"Name the {policy.label} link")

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
        org = _prompt_text("Org / group path (optional, press Enter to skip)")

    default_host = policy.default_host
    if provider == "youtrack":
        host = _prompt_text("Host (e.g. youtrack.example.com)", required=True)
    else:
        host = _prompt_text("Host", default=default_host) or default_host

    api_url = _compute_api_url_for_remote(provider, host)
    auth = resolve_auth(provider, api_url=api_url)
    token_env = _collect_token_source(auth)
    resolved_auth = resolve_auth(provider, token_env=token_env, api_url=api_url)

    remote = RemoteConfig(
        name=name,
        provider=provider,
        org=org,
        host=host,
        token_env=token_env,
        enabled=True,
        api_url=api_url,
    )
    print(f"  {_status('LINKED')} {_remote_call_sign(remote)} mapped to {host}")
    print(f"  {_status('SAFE')} token source: {token_source_label(resolved_auth, token_env)}")
    return remote


def _print_summary(config: SmithConfig, config_path: Path) -> None:
    print()
    _print_progress(len(_STAGES), len(_STAGES), "Launch checklist")
    _print_rule("SMITH // READY" if _ansi_enabled() else "Smith Ready")
    print()
    if not config.remotes:
        print("No remotes configured.")
        return

    remote_auth_scan = _scan_remote_auth(config.remotes.values())
    ready_count = sum(1 for item in remote_auth_scan.values() if item.status is AuthScanStatus.OK)
    _print_panel(
        "LAUNCH CARD" if _ansi_enabled() else "Summary",
        [
            f"Saved: {config_path}",
            f"Remotes linked: {len(config.remotes)}",
            f"Auth ready: {ready_count}/{len(config.remotes)}",
            "Posture: read-only investigations; tokens stay out of config.",
        ],
    )
    print()
    print(f"  {_accent(f'Remote roster ({len(config.remotes)})')}")
    for remote in config.remotes.values():
        label = _provider_label(remote.provider)
        org_info = f" org={remote.org}" if remote.org else ""
        auth_status = _status("OK") if remote_auth_scan[remote.name].status is AuthScanStatus.OK else _status("NEEDS")
        call_sign = _remote_call_sign(remote)
        print(f"    {auth_status:<9} {call_sign:<18} {label:<15} {org_info} host={remote.host}")
    print()
    print(f"  {_accent('Next move')}")
    print("    smith config list")
    first_remote = next(iter(config.remotes.values()), None)
    if first_remote and first_remote.provider != "youtrack":
        print(f"    smith {first_remote.name} repos")
    elif first_remote:
        print(f"    smith {first_remote.name} stories mine --take 5")


def _print_remote_list(remotes: dict[str, RemoteConfig]) -> None:
    if not remotes:
        print("  (no remotes configured)")
        return
    for remote in remotes.values():
        label = _provider_label(remote.provider)
        status = "enabled" if remote.enabled else "disabled"
        org_info = f" org={remote.org}" if remote.org else ""
        print(f"  - {remote.name} ({label}{org_info}, host={remote.host}, {status})")


def run_interactive_edit(config: SmithConfig, config_path: Path | None = None) -> SmithConfig:
    path = config_path or _default_config_path()
    remotes: dict[str, RemoteConfig] = dict(config.remotes)

    print()
    _print_rule("SMITH // CONFIG EDIT")
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

    provider_auth_scan = _scan_provider_auth()
    _print_boot_screen(path, provider_auth_scan)
    remote = _collect_remote(existing_names=set(remotes.keys()), provider_auth_scan=provider_auth_scan)
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
