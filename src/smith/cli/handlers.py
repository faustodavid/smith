#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from smith.client import SmithClient
from smith.formatting import dumps_json, make_envelope, render_text

EXIT_OK = 0
EXIT_INVALID_ARGS = 2
EXIT_AUTH_FAILURE = 3
EXIT_API_FAILURE = 4
EXIT_PARTIAL = 5


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _selected_providers(provider: str) -> list[str]:
    normalized = (provider or "azdo").strip().lower()
    if normalized == "all":
        return ["github", "gitlab", "azdo"]
    return [normalized]


def _requires_github_org(provider: str) -> bool:
    return "github" in _selected_providers(provider)


def _provider_is_configured(args: argparse.Namespace, provider: str) -> bool:
    if provider == "github":
        return bool(str(getattr(args, "github_org", "") or "").strip() or os.getenv("GITHUB_ORG", "").strip())
    if provider == "azdo":
        return bool(str(getattr(args, "azdo_org", "") or "").strip() or os.getenv("AZURE_DEVOPS_ORG", "").strip())
    if provider == "gitlab":
        cli_group = str(getattr(args, "gitlab_group", "") or "").strip().strip("/")
        env_group = os.getenv("GITLAB_GROUP", "").strip().strip("/")
        return bool(cli_group or env_group)
    return False


def _is_partial_result(data: Any) -> bool:
    if isinstance(data, dict) and "providers" in data:
        providers = data.get("providers", {})
        if not isinstance(providers, dict):
            return False
        for entry in providers.values():
            if not isinstance(entry, dict):
                continue
            if not bool(entry.get("ok", False)):
                return True
            if bool(entry.get("partial", False)):
                return True
            warnings = entry.get("warnings")
            if isinstance(warnings, list) and warnings:
                return True
        return False
    if isinstance(data, dict):
        if bool(data.get("partial", False)):
            return True
        warnings = data.get("warnings")
        if isinstance(warnings, list) and warnings:
            return True
    return False


def _cli_warnings(args: argparse.Namespace) -> list[str]:
    warnings: list[str] = []
    alias_used = str(getattr(args, "alias_used", "") or "").strip()
    primary_path = str(getattr(args, "primary_path", "") or "").strip()
    if alias_used:
        if primary_path:
            warnings.append(f"`{alias_used}` is deprecated; use `{primary_path}`.")
        else:
            warnings.append(f"`{alias_used}` is deprecated.")

    deprecated_flags = getattr(args, "deprecated_flags", None) or []
    if isinstance(deprecated_flags, list):
        for flag in deprecated_flags:
            flag_name = str(flag or "").strip()
            if not flag_name:
                continue
            if flag_name == "--repos":
                warnings.append("`--repos` is deprecated; repeat `--repo` instead.")
            else:
                warnings.append(f"`{flag_name}` is deprecated.")
    return warnings


def _command_meta(
    args: argparse.Namespace,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload_meta = dict(meta or {})
    alias_used = str(getattr(args, "alias_used", "") or "").strip()
    if alias_used:
        payload_meta["alias_used"] = alias_used

    deprecated_flags = getattr(args, "deprecated_flags", None) or []
    if isinstance(deprecated_flags, list) and deprecated_flags:
        payload_meta["deprecated_flags"] = [str(flag) for flag in deprecated_flags if str(flag).strip()]

    warnings = _cli_warnings(args)
    if warnings:
        payload_meta["warnings"] = warnings
    return payload_meta


def _emit_cli_warnings(args: argparse.Namespace) -> None:
    if getattr(args, "output_format", "text") != "text":
        return
    for warning in _cli_warnings(args):
        print(f"warning: {warning}", file=sys.stderr)


def validate_args_for_provider(args: argparse.Namespace) -> None:
    command = str(getattr(args, "command_id", ""))
    provider = str(getattr(args, "provider", "") or "").strip().lower()

    if not provider:
        return

    if command != "code.search" and provider == "all":
        raise ValueError(f"{command} does not support provider 'all'. Use azdo, github, or gitlab.")

    if command == "code.search" and not str(getattr(args, "query", "") or "").strip():
        raise ValueError('code search requires a query. Example: smith code search "grafana.*"')

    selected = _selected_providers(provider)
    if command == "code.search" and provider == "all":
        selected = [provider_name for provider_name in selected if _provider_is_configured(args, provider_name)]

    github_org = str(getattr(args, "github_org", "") or "").strip()
    azdo_org = str(getattr(args, "azdo_org", "") or "").strip()
    gitlab_group = str(getattr(args, "gitlab_group", "") or "").strip().strip("/")
    if "github" in selected and not os.getenv("GITHUB_ORG", "").strip() and not github_org:
        raise ValueError("Missing GITHUB_ORG. Example: export GITHUB_ORG=<org>  (or use --github-org)")
    if "azdo" in selected and not os.getenv("AZURE_DEVOPS_ORG", "").strip() and not azdo_org:
        raise ValueError(
            "Missing AZURE_DEVOPS_ORG. "
            "Example: export AZURE_DEVOPS_ORG=<your-org>  (or use --azdo-org)"
        )
    if "gitlab" in selected and not os.getenv("GITLAB_GROUP", "").strip().strip("/") and not gitlab_group:
        raise ValueError("Missing GITLAB_GROUP. Example: export GITLAB_GROUP=<group>  (or use --gitlab-group)")

    if command == "code.search" and provider == "github" and str(getattr(args, "project", "") or "").strip():
        raise ValueError("GitHub code search does not support `--project`. Use `--repo` instead.")
    if command == "code.search" and provider == "gitlab" and str(getattr(args, "project", "") or "").strip():
        raise ValueError("GitLab code search does not support `--project`. Use `--repo` instead.")


def _emit_success(
    *,
    args: argparse.Namespace,
    command: str,
    data: Any,
    meta: dict[str, Any] | None = None,
    partial: bool = False,
) -> int:
    payload_meta = _command_meta(args, meta)
    if args.output_format == "json":
        payload = make_envelope(ok=True, command=command, data=data, meta=payload_meta, error=None)
        print(dumps_json(payload))
    else:
        _emit_cli_warnings(args)
        print(render_text(command, data))

    if partial:
        return EXIT_PARTIAL
    return EXIT_OK


def _emit_error(
    *,
    args: argparse.Namespace,
    command: str,
    code: str,
    message: str,
    exit_code: int,
) -> int:
    payload_meta = _command_meta(args)
    if args.output_format == "json":
        payload = make_envelope(
            ok=False,
            command=command,
            data=None,
            meta=payload_meta,
            error={"code": code, "message": message},
        )
        print(dumps_json(payload))
    else:
        _emit_cli_warnings(args)
        print(message, file=sys.stderr)
    return exit_code


def _client_from_args(args: argparse.Namespace) -> SmithClient:
    return SmithClient(
        azdo_org=getattr(args, "azdo_org", None),
        github_org=getattr(args, "github_org", None),
        gitlab_group=getattr(args, "gitlab_group", None),
    )


def handle_cache_clean(client: SmithClient | None, args: argparse.Namespace) -> int:
    del client

    data = SmithClient.execute_cache_clean(provider=getattr(args, "cache_provider", "all"))
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=False,
    )


def handle_discover_projects(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_discover_projects(provider=args.provider)
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_discover_repos(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_discover_repos(provider=args.provider, project=getattr(args, "project", None))
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_code_search(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_code_search(
        provider=args.provider,
        query=args.query,
        project=args.project,
        repos=args.repos,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_code_grep(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_code_grep(
        provider=args.provider,
        project=getattr(args, "project", None),
        repo=getattr(args, "repo"),
        pattern=args.pattern,
        path=args.path,
        branch=args.branch,
        glob=args.glob,
        output_mode=args.output_mode,
        case_insensitive=not args.case_sensitive,
        context_lines=args.context_lines,
        from_line=args.from_line,
        to_line=args.to_line,
        no_clone=args.no_clone,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_pr_list(client: SmithClient, args: argparse.Namespace) -> int:
    if args.provider == "azdo":
        projects = [args.project]
        repos = [args.repo]
    else:
        projects = None
        repos = [args.repo]

    data = client.execute_pr_list(
        provider=args.provider,
        projects=projects,
        repos=repos,
        statuses=args.status,
        creators=args.creator,
        date_from=args.date_from,
        date_to=args.date_to,
        skip=args.skip,
        take=args.take,
        exclude_drafts=args.exclude_drafts,
        include_labels=args.include_labels,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_pr_get(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_pr_get(
        provider=args.provider,
        project=getattr(args, "project", None),
        repo=args.repo,
        pull_request_id=args.id,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_pr_threads(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_pr_threads(
        provider=args.provider,
        project=getattr(args, "project", None),
        repo=args.repo,
        pull_request_id=args.id,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_ci_logs(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_ci_logs(
        provider=args.provider,
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        build_id=args.id,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_ci_grep(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_ci_grep(
        provider=args.provider,
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        build_id=args.id,
        log_id=args.log_id,
        pattern=args.pattern,
        output_mode=args.output_mode,
        case_insensitive=not args.case_sensitive,
        context_lines=args.context_lines,
        from_line=args.from_line,
        to_line=args.to_line,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_work_get(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_work_get(
        provider=args.provider,
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        work_item_id=args.id,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_work_search(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_work_search(
        provider=args.provider,
        query=args.query,
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        area=args.area,
        work_item_type=args.type,
        state=args.state,
        assigned_to=args.assigned_to,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )


def handle_work_mine(client: SmithClient, args: argparse.Namespace) -> int:
    data = client.execute_work_mine(
        provider=args.provider,
        project=getattr(args, "project", None),
        repo=getattr(args, "repo", None),
        include_closed=args.include_closed,
        skip=args.skip,
        take=args.take,
    )
    return _emit_success(
        args=args,
        command=args.command_id,
        data=data,
        partial=_is_partial_result(data),
    )
