"""Credential redaction, applied to assembled structures.

Two lessons from an earlier project in this series are built into the shape of
this module, and both were defects before they were principles.

**Redact the assembled structure, not its inputs.** Anything derived from raw
text and attached afterwards — an excerpt, a diff, a summary, a failure detail
quoting the response — carries whatever the pass removed. So the entry point is
:func:`redact_structure`, which walks a finished object graph, and the callers
are arranged so that it runs last.

**Walk the structure; do not serialise and re-parse it.** Rendering to JSON,
redacting the text, and parsing it back mangles quoting, and redacts *keys* as
well as values, which produces reports whose field names are ``[REDACTED]``.

Every rule is named, and the names are reported alongside the count. A report
that says "3 redactions" and nothing else cannot be reviewed; one that says
``aws_access_key_id`` tells a reader what kind of mistake was made and where to
go looking for the source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: What replaces a matched credential. Fixed rather than length-preserving: a
#: mask that echoes the original length leaks the length.
PLACEHOLDER = "[REDACTED:{rule}]"


@dataclass(frozen=True, slots=True)
class Rule:
    """One credential shape."""

    name: str
    pattern: re.Pattern[str]
    #: Which capture group holds the secret. Group 0 replaces the whole match,
    #: which is right when the match *is* the credential and wrong when the
    #: match includes surrounding context that a reader needs to keep.
    group: int = 0


def _rule(name: str, pattern: str, *, group: int = 0, flags: re.RegexFlag = re.NOFLAG) -> Rule:
    return Rule(name=name, pattern=re.compile(pattern, flags), group=group)


#: Ordered: the more specific shapes run first so a provider key is reported as
#: that provider's key rather than as a generic high-entropy token.
RULES: tuple[Rule, ...] = (
    _rule(
        "private_key_block",
        r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----",
        flags=re.DOTALL,
    ),
    _rule("aws_access_key_id", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    _rule(
        "aws_secret_access_key",
        r"(?i)\baws_?secret_?access_?key\b\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?",
        group=1,
    ),
    _rule("github_token", r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    _rule("github_fine_grained_token", r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"),
    # Anthropic before OpenAI: an "sk-ant-…" key also matches the OpenAI shape,
    # and the first rule to fire is the one that names the finding.
    _rule("anthropic_api_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    _rule("openai_api_key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    _rule("google_api_key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    _rule("slack_token", r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b"),
    _rule("stripe_key", r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}\b"),
    _rule("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    _rule("bearer_token", r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{16,})", group=1),
    _rule("basic_auth_url", r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/:@]+:([^\s/@]{3,})@", group=1),
    # The catch-all. Deliberately last, deliberately narrow: an assignment to a
    # name that says "secret", with a value long enough not to be a placeholder.
    _rule(
        "assigned_secret",
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|access[_-]?key)\b\s*[=:]\s*"
        r"['\"]?([A-Za-z0-9_\-./+=]{12,})['\"]?",
        group=1,
    ),
)

#: Values the catch-all must not report. These are what people write when they
#: mean "there is no secret here", and reporting them trains reviewers to
#: dismiss findings.
_PLACEHOLDERS = re.compile(
    r"(?i)^(?:x{3,}|\*{3,}|\.{3,}|<[^>]+>|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|"
    r"replace[_-]?me|change[_-]?me|your[-_]?[\w-]*|placeholder|example|dummy|none|null|"
    r"redacted|\[redacted[^\]]*\]|"
    # Anywhere in the value, not only at the start: the convention across these
    # projects is "<something>-key-not-a-real-secret", and anchoring at the
    # front would report every one of them.
    r".*not[-_]?a[-_]?real[-_]?secret.*)$"
)


@dataclass(frozen=True, slots=True)
class Redaction:
    """The outcome of a redaction pass."""

    count: int
    rules: frozenset[str]

    @property
    def applied(self) -> bool:
        """Whether anything was replaced."""
        return self.count > 0

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {"applied": self.applied, "count": self.count, "rules": sorted(self.rules)}


def redact_text(text: str) -> tuple[str, Redaction]:
    """Replace credential-shaped substrings in *text*."""
    count = 0
    hit: set[str] = set()
    for rule in RULES:

        def replace(match: re.Match[str], rule: Rule = rule) -> str:
            nonlocal count
            secret = match.group(rule.group)
            if rule.name == "assigned_secret" and _PLACEHOLDERS.match(secret):
                return match.group(0)
            count += 1
            hit.add(rule.name)
            token = PLACEHOLDER.format(rule=rule.name)
            if rule.group == 0:
                return token
            # Keep the surrounding context: `Authorization: Bearer [REDACTED:…]`
            # is reviewable, a bare placeholder is not.
            whole = match.group(0)
            start = match.start(rule.group) - match.start(0)
            return whole[:start] + token + whole[start + len(secret) :]

        text = rule.pattern.sub(replace, text)
    return text, Redaction(count=count, rules=frozenset(hit))


def redact_structure(value: Any) -> tuple[Any, Redaction]:
    """Walk *value*, redacting every string in it.

    Dictionary **keys are not redacted**. A key is a field name chosen by this
    package, not user data, and replacing one produces a report nothing can
    read.
    """
    count = 0
    rules: set[str] = set()

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, str):
            cleaned, result = redact_text(node)
            count += result.count
            rules.update(result.rules)
            return cleaned
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, tuple):
            return tuple(walk(item) for item in node)
        return node

    return walk(value), Redaction(count=count, rules=frozenset(rules))
