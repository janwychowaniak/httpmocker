#!/usr/bin/env bash
# pre-commit stage: block commits whose staged changes contain a secret.
command -v gitleaks >/dev/null 2>&1 || { echo "⚠️  gitleaks not installed — skipping" >&2; exit 0; }
gitleaks protect --staged --config .gitleaks.toml --redact -v || {   # v8.19+: gitleaks git --staged
  echo "❌ gitleaks: secret in staged changes — commit blocked" >&2; exit 1; }
