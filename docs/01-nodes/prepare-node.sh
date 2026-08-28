#!/usr/bin/env bash
#
# prepare-node.sh — bring one machine to the state where it can join the cluster.
#
#   Usage:  ./prepare-node.sh <ip-address> <expected-hostname>
#
# Run this from your workstation, once per machine, BEFORE installing k3s on it.
# It is deliberately short and deliberately a template: read it, edit it, own it.
#
# Assumes a Debian-family OS and that you can already SSH to the machine as
# <ADMIN_USER>. If you cannot yet, do that by hand first — bootstrapping SSH
# access from a password inside a script is a good way to leave a password in a
# script, and this repo would rather you did not.
#
# Idempotent: safe to re-run.

set -euo pipefail

NODE_IP="${1:?usage: prepare-node.sh <ip-address> <expected-hostname>}"
NODE_NAME="${2:?usage: prepare-node.sh <ip-address> <expected-hostname>}"

# ---------------------------------------------------------------------------
# Edit these for your cluster.
# ---------------------------------------------------------------------------
ADMIN_USER="<ADMIN_USER>"
SSH_PUBKEY_FILE="${HOME}/.ssh/id_ed25519.pub"   # the key that will drive the build

SSH="ssh -o StrictHostKeyChecking=accept-new ${ADMIN_USER}@${NODE_IP}"

# ---------------------------------------------------------------------------
# Guard: is this the machine you think it is?
#
# Worth the three lines. Preparing the wrong machine — a laptop that took the
# address you meant for the desktop — is a genuinely bad afternoon, and DHCP
# does not care about your intentions.
# ---------------------------------------------------------------------------
ACTUAL="$(${SSH} hostname)"
if [[ "${ACTUAL}" != "${NODE_NAME}" ]]; then
  echo "ABORT: ${NODE_IP} reports hostname '${ACTUAL}', expected '${NODE_NAME}'." >&2
  echo "Check the DHCP reservation before retrying." >&2
  exit 1
fi

echo "=== Preparing ${NODE_NAME} (${NODE_IP}) ==="

# ---------------------------------------------------------------------------
# Install the build key first, so everything after this is key-based.
# ---------------------------------------------------------------------------
if [[ -f "${SSH_PUBKEY_FILE}" ]]; then
  ${SSH} "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
  ${SSH} "grep -qxF '$(cat "${SSH_PUBKEY_FILE}")' ~/.ssh/authorized_keys || echo '$(cat "${SSH_PUBKEY_FILE}")' >> ~/.ssh/authorized_keys"
else
  echo "WARNING: ${SSH_PUBKEY_FILE} not found — skipping key install." >&2
fi

# ---------------------------------------------------------------------------
# Everything else, in one remote shell.
# ---------------------------------------------------------------------------
${SSH} "sudo bash -s" <<REMOTE
set -euo pipefail

# --- passwordless sudo, so later automation never stalls on a prompt --------
echo "${ADMIN_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-${ADMIN_USER}
chmod 440 /etc/sudoers.d/90-${ADMIN_USER}

# --- hostname ---------------------------------------------------------------
# Kubernetes takes the node name from here and it is unpleasant to change later.
hostnamectl set-hostname "${NODE_NAME}"

# --- swap off, and off after a reboot ---------------------------------------
# The second line is the one that matters; without it swap returns at boot and
# the kubelet complains about a machine that was fine yesterday.
swapoff -a
sed -i.bak '/[[:space:]]swap[[:space:]]/d' /etc/fstab

# --- iSCSI, for the storage layer in 03-storage/ ----------------------------
# Doing this now costs one line. Discovering it later costs an afternoon of
# volumes that will not attach, with an error that names none of this.
apt-get update -qq
apt-get install -y open-iscsi curl nfs-common chrony
systemctl enable --now iscsid
printf 'iscsi_tcp\nlibiscsi\nlibiscsi_tcp\nscsi_transport_iscsi\n' > /etc/modules-load.d/iscsi.conf
modprobe iscsi_tcp libiscsi libiscsi_tcp scsi_transport_iscsi || true

# --- clocks ------------------------------------------------------------------
# Certificate validity, token expiry and log correlation all depend on this,
# and all three fail with errors that mention something else entirely.
systemctl enable --now chrony

# --- laptops: ignore the lid -------------------------------------------------
# Half of these machines have lids. A node that suspends when somebody closes
# it is not a node.
sed -i 's/^#*HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
grep -q '^HandleLidSwitch=ignore' /etc/systemd/logind.conf || echo 'HandleLidSwitch=ignore' >> /etc/systemd/logind.conf
systemctl restart systemd-logind
REMOTE

# ---------------------------------------------------------------------------
# Report the things you must check yourself.
#
# These are not automated on purpose: the resolver setting in particular
# depends on which network stack your OS shipped with, and getting it wrong
# silently breaks in-cluster DNS on this node only. See findings/what-did-not.md.
# ---------------------------------------------------------------------------
echo
echo "=== ${NODE_NAME} prepared. Check these before joining it: ==="
echo
echo "--- /etc/resolv.conf (want 'domain', NOT 'search', and the same on every node) ---"
${SSH} "cat /etc/resolv.conf"
echo
echo "--- swap (want 0) ---"
${SSH} "free -h | awk '/Swap/ {print \$2}'"
echo
echo "--- clock (want 'System clock synchronized: yes') ---"
${SSH} "timedatectl | grep -i synchronized"
echo
echo "--- iscsid (want 'active') ---"
${SSH} "systemctl is-active iscsid"
echo
echo "A reboot here is worth it: it proves swap stays off and the machine comes"
echo "back without you. Then join it — see README.md section 4."
