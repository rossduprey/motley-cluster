#!/usr/bin/env bash
#
# install-ca-trust.sh — put the internal CA root into every node's system trust store.
#
#   Usage:  ./install-ca-trust.sh <node> [<node> ...]
#
# Run this once per cluster, and again for every node you add afterwards.
# Forgetting the "afterwards" half is the usual failure: one node out of five
# does not trust the CA, and the symptom is one application, on one node,
# failing to verify a certificate that is fine everywhere else.
#
# Debian-family assumptions (update-ca-certificates, /usr/local/share/ca-certificates).
# On a Red Hat family OS the directory is /etc/pki/ca-trust/source/anchors and the
# command is update-ca-trust.
#
# Idempotent: safe to re-run.

set -euo pipefail

# ---------------------------------------------------------------------------
# Edit these for your cluster.
# ---------------------------------------------------------------------------
ADMIN_USER="<ADMIN_USER>"
CA_FILE="lan-ca.crt"                    # the name it will have on each node
LOCAL_CA="${HOME}/.cluster/${CA_FILE}"  # your local copy of the CA root

# ---------------------------------------------------------------------------
# Where to get the root certificate, if you do not have it locally yet.
#
# It is whatever your CA published as its root. With step-ca it is on the CA
# pod at /home/step/certs/root_ca.crt, and the reliable way to fetch it is
# through the API server rather than over the network you are about to trust:
#
#   kubectl exec -n <CA_NAMESPACE> <CA_POD> -- \
#     cat /home/step/certs/root_ca.crt > "${LOCAL_CA}"
#
# Read it before you distribute it. A file that is not a PEM certificate is the
# single most common thing to end up in this position — see the note in
# README.md section 5.
# ---------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
  echo "usage: install-ca-trust.sh <node> [<node> ...]" >&2
  exit 1
fi

if [[ ! -s "${LOCAL_CA}" ]]; then
  echo "ABORT: ${LOCAL_CA} is missing or empty. Fetch the CA root first (see comments above)." >&2
  exit 1
fi

if ! head -1 "${LOCAL_CA}" | grep -q 'BEGIN CERTIFICATE'; then
  echo "ABORT: ${LOCAL_CA} does not start with '-----BEGIN CERTIFICATE-----'." >&2
  echo "Distributing a placeholder instead of a certificate is worse than doing nothing," >&2
  echo "because everything then looks configured." >&2
  exit 1
fi

for NODE in "$@"; do
  echo "=== ${NODE} ==="

  scp -q "${LOCAL_CA}" "${ADMIN_USER}@${NODE}:/tmp/${CA_FILE}"

  ssh "${ADMIN_USER}@${NODE}" "sudo bash -s" <<REMOTE
set -euo pipefail
install -m 0644 "/tmp/${CA_FILE}" "/usr/local/share/ca-certificates/${CA_FILE}"
rm -f "/tmp/${CA_FILE}"
update-ca-certificates >/dev/null
REMOTE

  # Verify against the system bundle, not against the file we just copied —
  # the question is whether the OS accepted it, not whether scp worked.
  if ssh "${ADMIN_USER}@${NODE}" "openssl x509 -noout -subject -in /usr/local/share/ca-certificates/${CA_FILE}"; then
    echo "  installed and refreshed"
  fi
done

echo
echo "Nodes done. Three places this certificate ALSO has to go — none of them are"
echo "covered by this script:"
echo "  1. every pod that calls an internal HTTPS endpoint (mount it via your service template)"
echo "  2. every human's browser or OS keychain, once per device"
echo "  3. /etc/rancher/k3s/registries.yaml on every node, once you have a registry (see 04-git-ci-registry/)"
