#!/usr/bin/env bash
#
# check-anonymized.sh — fail if anything identifying or secret is about to be published.
#
# This repo is a template. Every value a reader must supply appears as an obvious
# <SCREAMING_ANGLE_CAPS> placeholder. This script is the mechanical guard that keeps it
# that way: it scans tracked files for real-looking values and exits non-zero on a hit.
#
# It runs in two layers:
#
#   1. GENERIC patterns (below, public)  — private IPs, emails, key material, tokens,
#      internal hostnames. Useful to anyone publishing infrastructure docs, which is why
#      they live in the repo rather than in someone's shell history.
#
#   2. A LOCAL denylist (.anonymize-denylist.local, gitignored, optional) — the author's
#      own hostnames, domains, usernames and passwords. That file is deliberately NOT
#      committed: a denylist of real values IS the leak it is trying to prevent.
#
# Usage:
#   scripts/check-anonymized.sh            # scan tracked files
#   scripts/check-anonymized.sh --all      # scan the working tree too (untracked included)
#
# Exceptions live in .anonymize-allow (committed): one extended-regex per line. Use it for
# documentation examples that legitimately look like real values (RFC 5737 / RFC 3849
# addresses, example.com, and so on). Keep it short and justify each entry in a comment.

set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 2

MODE="tracked"
[[ "${1:-}" == "--all" ]] && MODE="all"

SELF="scripts/check-anonymized.sh"
ALLOW_FILE=".anonymize-allow"
LOCAL_DENY=".anonymize-denylist.local"

# ---------------------------------------------------------------------------
# Generic patterns: label<TAB>extended-regex
# ---------------------------------------------------------------------------
read -r -d '' PATTERNS <<'EOF'
private-ipv4-192	(^|[^0-9.])192\.168\.[0-9]{1,3}\.[0-9]{1,3}
private-ipv4-10	(^|[^0-9.])10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}
private-ipv4-172	(^|[^0-9.])172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}
email-address	[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}
internal-tld	[a-z0-9-]+\.(lan|local|internal|home|localdomain)([^a-z0-9-]|$)
github-pat	gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}
generic-api-key	(gsk|sk|pk|xox[baprs]|AIza)[-_][A-Za-z0-9_-]{16,}
aws-key-id	AKIA[0-9A-Z]{16}
private-key-block	-----BEGIN [A-Z ]*PRIVATE KEY-----
ssh-public-key	ssh-(rsa|ed25519|dss) AAAA[0-9A-Za-z+/]{20,}
long-hex-secret	(^|[^0-9a-fA-F])[0-9a-fA-F]{32,}([^0-9a-fA-F]|$)
basic-auth-in-url	[a-z]+://[A-Za-z0-9._%+-]+:[^@/[:space:]]+@
password-assignment	(?i)
EOF

# The last entry is a placeholder the loop skips; keyword scanning is handled separately
# below because it needs case-insensitive matching with a value on the right-hand side.
KEYWORD_RE='(password|passwd|secret|token|api[_-]?key|bearer)[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9._/+-]{8,}'

# ---------------------------------------------------------------------------
# File list
# ---------------------------------------------------------------------------
if [[ "$MODE" == "all" ]]; then
  mapfile -t FILES < <(git ls-files --cached --others --exclude-standard)
else
  mapfile -t FILES < <(git ls-files)
fi

# Never scan the guard itself (it necessarily contains the patterns), the local denylist
# (it necessarily contains the real values), or the allow file.
FILTERED=()
for f in "${FILES[@]}"; do
  [[ "$f" == "$SELF" || "$f" == "$LOCAL_DENY" || "$f" == "$ALLOW_FILE" ]] && continue
  [[ -f "$f" ]] || continue
  # skip binaries
  if grep -Iq . "$f" 2>/dev/null; then FILTERED+=("$f"); fi
done

if [[ ${#FILTERED[@]} -eq 0 ]]; then
  echo "check-anonymized: no files to scan"
  exit 0
fi

# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------
filter_allowed() {
  if [[ -f "$ALLOW_FILE" ]]; then
    local allow
    allow=$(grep -vE '^\s*(#|$)' "$ALLOW_FILE" || true)
    if [[ -n "$allow" ]]; then
      grep -vE "$(echo "$allow" | paste -sd'|' -)" || true
      return
    fi
  fi
  cat
}

FAILED=0
report() {
  local label="$1" hits="$2"
  if [[ -n "$hits" ]]; then
    FAILED=1
    echo ""
    echo "✗ $label"
    echo "$hits" | sed 's/^/    /'
  fi
}

# ---------------------------------------------------------------------------
# Generic scan
# ---------------------------------------------------------------------------
while IFS=$'\t' read -r label regex; do
  [[ -z "${label:-}" || -z "${regex:-}" ]] && continue
  [[ "$label" == "password-assignment" ]] && continue
  hits=$(grep -nEH "$regex" "${FILTERED[@]}" 2>/dev/null | filter_allowed)
  report "$label" "$hits"
done <<< "$PATTERNS"

hits=$(grep -nEHi "$KEYWORD_RE" "${FILTERED[@]}" 2>/dev/null | filter_allowed)
report "password-assignment" "$hits"

# ---------------------------------------------------------------------------
# Local denylist — the author's own identifying strings
# ---------------------------------------------------------------------------
if [[ -f "$LOCAL_DENY" ]]; then
  while IFS= read -r term; do
    [[ -z "$term" || "$term" =~ ^[[:space:]]*# ]] && continue
    hits=$(grep -nEHi -- "$term" "${FILTERED[@]}" 2>/dev/null | filter_allowed)
    report "local-denylist: $term" "$hits"
  done < "$LOCAL_DENY"
else
  echo "check-anonymized: note — no $LOCAL_DENY found; generic patterns only."
  echo "  If you are adapting docs from a real cluster, create it (it is gitignored)."
fi

echo ""
if [[ $FAILED -ne 0 ]]; then
  cat <<'MSG'
────────────────────────────────────────────────────────────────────────
FAILED — the tree contains values that look real.

Replace each with an obvious placeholder, e.g.:
    192.168.1.10          ->  <NODE_A_IP>
    node1.example.lan     ->  <NODE_A>.<CLUSTER_DOMAIN>
    admin@example.com     ->  <ADMIN_EMAIL>

If a hit is a legitimate documentation example, add a justified regex to
.anonymize-allow rather than weakening a pattern.
────────────────────────────────────────────────────────────────────────
MSG
  exit 1
fi

echo "✓ check-anonymized: clean (${#FILTERED[@]} files scanned)"
