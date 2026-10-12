#!/usr/bin/env bash
# pre-push stage: block pushes whose new commits contain a secret, in content or
# in messages. pre-commit does not pass the per-ref stdin of git's pre-push hook
# on to hooks, so scan every local commit not yet on any remote: a superset of
# what is being pushed that also covers multi-ref pushes.
command -v gitleaks >/dev/null 2>&1 || { echo "⚠️  gitleaks not installed — skipping" >&2; exit 0; }
fail=0
tmp=$(mktemp -d) || exit 0; trap 'rm -rf "$tmp"' EXIT
range="--branches --tags --not --remotes"
gitleaks detect --log-opts="$range" --config .gitleaks.toml --redact -v || fail=1   # v8.19+: gitleaks git
# shellcheck disable=SC2086  # $range must word-split into separate rev options
git log --format=%B $range > "$tmp/m" 2>/dev/null
[ -s "$tmp/m" ] && { gitleaks detect --no-git --source "$tmp/m" --config .gitleaks.toml --redact -v || fail=1; }
[ "$fail" -eq 0 ] || { echo "❌ push blocked (see finding above)" >&2; exit 1; }
