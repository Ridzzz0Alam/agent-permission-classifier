"""Deterministic tier router (no LLM).

Tier 1  Read-only tools (Read, Grep, Glob, ...) plus the user's always-allow rules.
        Any rule that would grant arbitrary code execution is stripped when the rules load.
Tier 2  Edit/Write whose resolved path is strictly inside project_dir.
Tier 3  Everything else goes to the LLM classifier.

All decisions are made on TEXT. Nothing here touches the real filesystem or runs a command:
the dataset's paths are fictional, and its commands are data only.

Notes on choices:
- Following the source post, Tier 1 allows reads anywhere, including outside the project.
  Reading alone changes no state; sending what was read somewhere is a separate, classified action.
- Symlinks: there is no real filesystem to call realpath() on, so links are reconstructed from
  `ln` commands in the session's earlier Bash calls (parsed with shlex, never run). If a session
  makes links we cannot parse with confidence, every Edit/Write goes to Tier 3 (fail closed).
- Protected in-project paths (.git/, .claude/) go to Tier 3 even though they are inside the
  project: a write there can install hooks or change the agent's own permissions.
"""

from __future__ import annotations

import fnmatch
import logging
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlsplit

import yaml

log = logging.getLogger(__name__)

TIER1_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead", "TodoWrite"})
TIER2_TOOLS = frozenset({"Edit", "Write"})
RULE_TOOLS = frozenset({"Bash", "WebFetch"}) | TIER1_TOOLS
PROTECTED_PROJECT_DIRS = (".git", ".claude")


@dataclass(frozen=True)
class TierDecision:
    tier: int  # 1, 2 or 3
    allowed: bool  # True: allowed without an LLM call. False: send to the classifier.
    reason: str
    matched_rule: str | None = None


# ---------------------------------------------------------------- always-allow rules

@dataclass(frozen=True)
class Rule:
    raw: str
    tool: str
    pattern: str | None  # None means the rule covers the whole tool


@dataclass
class LoadedRules:
    kept: list[Rule] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (raw rule, reason)


_RULE_RE = re.compile(r"^\s*([A-Za-z]+)\s*(?:\((.*)\))?\s*$", re.S)
_WILDCARD_CHARS = "*?["

# Commands that can run arbitrary code when given open-ended (wildcarded) arguments.
INTERPRETERS = frozenset({
    "python", "pypy", "node", "nodejs", "deno", "bun", "ruby", "irb", "perl", "php", "lua",
    "luajit", "rscript", "julia", "java", "jshell", "osascript", "pwsh", "powershell", "tclsh",
    "guile", "ghci", "runghc", "scala", "groovy", "dotnet",
})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "busybox"})
WRAPPERS = frozenset({  # run their arguments as a command, or can execute code
    "eval", "exec", "source", ".", "xargs", "env", "sudo", "doas", "su", "nohup", "timeout",
    "nice", "ionice", "time", "watch", "stdbuf", "setsid", "chroot", "ssh", "docker", "podman",
    "kubectl", "find", "awk", "gawk", "sed", "parallel", "script", "expect", "command", "builtin",
})
RUNNERS = frozenset({  # build/task runners and one-shot package executors
    "make", "gmake", "just", "rake", "task", "gradle", "gradlew", "mvn", "ant", "ninja",
    "invoke", "nox", "tox", "npx", "uvx", "pnpx", "bunx", "pipx",
})
EXEC_IF_WILDCARDED = INTERPRETERS | SHELLS | WRAPPERS | RUNNERS
PKG_MANAGERS = frozenset({
    "npm", "yarn", "pnpm", "bun", "uv", "poetry", "pipenv", "pdm", "hatch", "cargo", "go",
    "bundle", "deno", "composer",
})
RUN_SUBCOMMANDS = frozenset({"run", "run-script", "exec", "x", "dlx", "task", "start"})


def _cmd_name(token: str) -> str:
    """'/usr/bin/python3.12' -> 'python'; './gradlew' -> 'gradlew'."""
    name = posixpath.basename(token).lower()
    stripped = re.sub(r"[\d.]+$", "", name)
    return stripped or name


def parse_rule(raw: str) -> Rule | None:
    m = _RULE_RE.match(raw)
    if not m:
        return None
    pattern = m.group(2)
    return Rule(raw=raw.strip(), tool=m.group(1), pattern=pattern.strip() if pattern is not None else None)


def drop_reason(rule: Rule) -> str | None:
    """Why this rule must be stripped, or None if it is safe to keep."""
    if rule.tool not in RULE_TOOLS:
        return f"always-allow rules are not supported for tool {rule.tool!r}"

    if rule.tool == "WebFetch":
        if rule.pattern is not None and not rule.pattern.startswith("domain:"):
            return "WebFetch rules must have the form WebFetch(domain:host)"
        return None

    if rule.tool != "Bash":
        return None

    pat = rule.pattern
    if pat is None or pat.strip(" *:?") == "":
        return "blanket shell access"
    body = pat[:-2] if pat.endswith(":*") else pat  # 'ls:*' -> command name 'ls'
    first = body.split()[0]
    if any(c in first for c in _WILDCARD_CHARS):
        return "wildcard in the command name (could match any interpreter)"
    has_wild = any(c in pat for c in _WILDCARD_CHARS)
    if not has_wild:
        return None
    name = _cmd_name(first)
    if name in EXEC_IF_WILDCARDED:
        return f"wildcarded interpreter/shell/runner ({name})"
    if name in PKG_MANAGERS:
        literal = re.split(r"[*?\[]", pat, maxsplit=1)[0].replace(":", " ").split()
        if len(literal) <= 1 or literal[1] in RUN_SUBCOMMANDS:
            return f"package-manager run command ({name})"
    return None


def sanitize_rules(raw_rules: Iterable[str]) -> LoadedRules:
    out = LoadedRules()
    for raw in raw_rules:
        rule = parse_rule(str(raw))
        reason = "unparseable rule" if rule is None else drop_reason(rule)
        if reason:
            out.dropped.append((str(raw), reason))
            log.warning("dropped always-allow rule %r: %s", raw, reason)
        else:
            out.kept.append(rule)
    return out


def load_allow_rules(path: str | Path) -> LoadedRules:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return sanitize_rules(data.get("always_allow") or [])


# Any of these in a command means it is more than one simple command.
_SHELL_META = re.compile(r"[;&|`<>\n\r$\\()]")


def _norm_ws(s: str) -> str:
    return " ".join(s.split())


def bash_rule_matches(pattern: str, command: str) -> bool:
    cmd = _norm_ws(command)
    if not cmd or _SHELL_META.search(cmd):
        return False  # chains, pipes, substitutions, redirects never match an allow rule
    pat = _norm_ws(pattern)
    if pat.endswith(":*"):
        prefix = pat[:-2].rstrip()
        return cmd == prefix or cmd.startswith(prefix + " ")
    if any(c in pat for c in _WILDCARD_CHARS):
        return fnmatch.fnmatchcase(cmd, pat)
    return cmd == pat


def webfetch_rule_matches(pattern: str | None, url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    if pattern is None:
        return True
    domain = pattern[len("domain:"):].strip().lower().rstrip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain))


def rule_matches(rule: Rule, tool: str, inp: Mapping) -> bool:
    if rule.tool != tool:
        return False
    if tool == "Bash":
        return rule.pattern is not None and bash_rule_matches(rule.pattern, str(inp.get("command", "")))
    if tool == "WebFetch":
        return webfetch_rule_matches(rule.pattern, str(inp.get("url", "")))
    return rule.pattern is None  # Tier-1 tool named in a rule: Tier 1 covers it anyway


# ---------------------------------------------------------------- paths and symlinks

def _norm_abs(path: str) -> str:
    p = posixpath.normpath(path)
    return "/" + p.lstrip("/")  # normpath keeps a leading '//'; collapse it


def virtual_realpath(path: str, symlinks: Mapping[str, str]) -> str | None:
    """realpath() against a symlink map {absolute link path: target} instead of the disk.
    Returns None on a link loop."""
    pending = [c for c in path.split("/") if c]
    out: list[str] = []
    hops = 0
    while pending:
        c = pending.pop(0)
        if c == ".":
            continue
        if c == "..":
            if out:
                out.pop()
            continue
        candidate = "/" + "/".join(out + [c])
        if candidate in symlinks:
            hops += 1
            if hops > 40:
                return None
            target = symlinks[candidate]
            if target.startswith("/"):
                out = []
            pending = [x for x in target.split("/") if x] + pending
            continue
        out.append(c)
    return "/" + "/".join(out)


def resolve_path(path: str, project_dir: str, symlinks: Mapping[str, str] | None = None) -> str | None:
    """Absolute, normalised, symlink-resolved path, or None if it cannot be determined from
    the text alone (~, $VAR, backticks, backslashes, NUL, empty)."""
    if not path or any(ch in path for ch in ("\x00", "\\", "$", "`")) or path.startswith("~"):
        return None
    base = _norm_abs(project_dir)
    p = path if path.startswith("/") else posixpath.join(base, path)
    return virtual_realpath(p, symlinks or {})


_LN_WORD = re.compile(r"(^|[^\w-])ln($|[^\w-])")
_OTHER_LINKERS = re.compile(r"symlink|\bmount\b|\bcp\s+-\w*s|\bln\b|mklink|bindfs", re.I)
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")


def symlinks_from_session(prior_tool_calls: Iterable[Mapping], project_dir: str) -> tuple[dict[str, str], bool]:
    """Reconstruct links that earlier Bash calls created (hard links are treated like symlinks).
    Returns (map, uncertain). uncertain=True means some link could not be parsed reliably."""
    base = _norm_abs(project_dir)
    links: dict[str, str] = {}
    uncertain = False
    for call in prior_tool_calls or []:
        if call.get("tool") != "Bash":
            continue
        cmd = str((call.get("input") or {}).get("command", ""))
        if not _OTHER_LINKERS.search(cmd):
            continue
        if not _LN_WORD.search(cmd):
            uncertain = True  # symlink made by some other means (python os.symlink, mount ...)
            continue
        parsed_any = False
        segments = _SEGMENT_SPLIT.split(cmd)
        changes_dir = any(seg.strip().startswith(("cd ", "pushd ")) for seg in segments)
        for seg in segments:
            try:
                toks = shlex.split(seg)
            except ValueError:
                uncertain = True
                continue
            if not toks or posixpath.basename(toks[0]) != "ln":
                continue
            parsed_any = True
            if changes_dir:
                uncertain = True  # links are relative to a cwd we cannot track
                continue
            symbolic, args, ok = False, [], True
            end_of_opts = False
            for t in toks[1:]:
                if not end_of_opts and t == "--":
                    end_of_opts = True
                elif not end_of_opts and t.startswith("--"):
                    if t.startswith("--target-directory") or t == "--relative":
                        ok = False
                    symbolic |= t == "--symbolic"
                elif not end_of_opts and t.startswith("-") and len(t) > 1:
                    if "t" in t[1:] or "r" in t[1:]:
                        ok = False
                    symbolic |= "s" in t[1:]
                else:
                    args.append(t)
            if not ok or not args or len(args) > 2 or (len(args) == 2 and args[1].endswith("/")):
                uncertain = True
                continue
            target = args[0]
            link = args[1] if len(args) == 2 else posixpath.basename(target.rstrip("/"))
            if any(ch in target + link for ch in "$`~*?"):
                uncertain = True
                continue
            link_abs = _norm_abs(link if link.startswith("/") else posixpath.join(base, link))
            if not symbolic and not target.startswith("/"):
                target = posixpath.join(base, target)  # hard-link targets are relative to cwd
            links[link_abs] = target
        if not parsed_any:
            uncertain = True  # 'ln' appears, e.g. inside bash -c "..." or $(...)
    return links, uncertain


# ---------------------------------------------------------------- router

def route(
    action: Mapping,
    project_dir: str,
    rules: Iterable[Rule] = (),
    prior_tool_calls: Iterable[Mapping] = (),
) -> TierDecision:
    tool = action.get("tool")
    inp = action.get("input") or {}

    if tool in TIER1_TOOLS:
        return TierDecision(1, True, f"{tool} is a read-only tool")

    for rule in rules:
        if rule_matches(rule, tool, inp):
            return TierDecision(1, True, "matched a user always-allow rule", rule.raw)

    if tool in TIER2_TOOLS:
        raw_path = inp.get("file_path") or inp.get("path") or ""
        links, uncertain = symlinks_from_session(prior_tool_calls, project_dir)
        if uncertain:
            return TierDecision(3, False, "session created links that cannot be resolved from text")
        resolved = resolve_path(str(raw_path), project_dir, links)
        if resolved is None:
            return TierDecision(3, False, f"path {raw_path!r} cannot be resolved from text")
        proj = _norm_abs(project_dir)
        if not resolved.startswith(proj.rstrip("/") + "/"):
            return TierDecision(3, False, f"{tool} target {resolved} is outside the project")
        first = resolved[len(proj.rstrip("/")) + 1:].split("/")[0]
        if first in PROTECTED_PROJECT_DIRS:
            return TierDecision(3, False, f"{tool} target is in the protected path {first}/")
        return TierDecision(2, True, f"{tool} inside the project ({resolved})")

    return TierDecision(3, False, f"{tool} requires the classifier")
