#!/usr/bin/env bash
#
# prepare-node.sh — bring one machine to the state where it can join the cluster.
#
#   Usage:  ./prepare-node.sh <ip-address> <expected-hostname>
#
# Run this from your workstation, once per machine, BEFORE installing k3s on it.
# It is deliberately short and deliberately a template: read it, edit it, own it.
#
# Assumes you can already SSH to the machine as <ADMIN_USER>. If you cannot yet,
# do that by hand first — bootstrapping SSH access from a password inside a
# script is a good way to leave a password in a script, and this repo would
# rather you did not.
#
# Package installation is dispatched on what the machine actually has:
# apt-get, dnf, zypper, or transactional-update on an immutable/transactional
# root (openSUSE MicroOS and relatives). On a transactional system the packages
# land in a new snapshot and DO NOT EXIST until the machine reboots — the
# script says so at the end. Everything else it does takes effect immediately.
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

# The time source every node must agree with. Point this at your gateway, not a
# public pool — see 00-premise/prerequisites.md item 7 for why "NTP is running"
# is not the same claim as "our clocks agree".
NTP_SERVER="<GATEWAY_IP>"

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

# --- work out how this machine installs packages ----------------------------
# A transactional root is detected first, because such a system ALSO has zypper
# or dnf on it and using them directly on a read-only root either fails or
# writes somewhere the next reboot discards.
PKG=""
if command -v transactional-update >/dev/null 2>&1; then
  PKG="transactional"
elif command -v apt-get >/dev/null 2>&1; then
  PKG="apt"
elif command -v dnf >/dev/null 2>&1; then
  PKG="dnf"
elif command -v zypper >/dev/null 2>&1; then
  PKG="zypper"
else
  echo "ABORT: no supported package manager found on \$(hostname)." >&2
  exit 1
fi
echo "package manager: \${PKG}"

# The time daemon differs by family: chrony on Debian/Fedora, systemd-timesyncd
# as shipped on MicroOS. Resolved below, after packages, from what is present.

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
[ -f /etc/fstab ] && sed -i.bak '/[[:space:]]swap[[:space:]]/d' /etc/fstab
# Some images enable a swap unit or zram rather than an fstab entry, in which
# case deleting the fstab line removes nothing and swap is back after a reboot.
systemctl list-unit-files --type=swap --no-legend 2>/dev/null | awk '{print \$1}' | while read -r u; do
  systemctl mask "\$u" || true
done
systemctl disable --now dev-zram0.swap systemd-zram-setup@zram0.service 2>/dev/null || true

# --- iSCSI, for the storage layer in 03-storage/ ----------------------------
# Doing this now costs one line. Discovering it later costs an afternoon of
# volumes that will not attach, with an error that names none of this.
case "\${PKG}" in
  apt)
    apt-get update -qq
    apt-get install -y open-iscsi curl nfs-common chrony
    ;;
  dnf)
    dnf install -y iscsi-initiator-utils curl nfs-utils chrony
    ;;
  zypper)
    zypper --non-interactive install open-iscsi curl nfs-client chrony
    ;;
  transactional)
    # Applies into a new snapshot. Nothing installed here exists on the running
    # system; it appears after the reboot this script asks for at the end.
    # --continue stacks onto the pending snapshot so a re-run does not discard
    # the previous one.
    transactional-update --non-interactive --continue pkg install \\
      open-iscsi curl nfs-client || true
    ;;
esac

# Enabling a unit whose package is only in a pending snapshot fails, and that is
# not an error worth aborting on — the reboot is what completes this node.
if systemctl list-unit-files iscsid.service >/dev/null 2>&1; then
  systemctl enable --now iscsid || systemctl enable iscsid || true
fi
printf 'iscsi_tcp\nlibiscsi\nlibiscsi_tcp\nscsi_transport_iscsi\n' > /etc/modules-load.d/iscsi.conf
modprobe iscsi_tcp libiscsi libiscsi_tcp scsi_transport_iscsi || true

# --- clocks ------------------------------------------------------------------
# Certificate validity, token expiry and log correlation all depend on this,
# and all three fail with errors that mention something else entirely.
#
# Point at ONE server you control rather than a public pool. A node syncing to
# the internet answers "is NTP running" and not "do our clocks agree".
NTP="${NTP_SERVER}"
if [ "\${NTP}" = "<GATEWAY_IP>" ]; then
  echo "WARNING: NTP_SERVER is still the placeholder — leaving time config alone." >&2
elif command -v chronyd >/dev/null 2>&1; then
  mkdir -p /etc/chrony.d /etc/chrony/conf.d 2>/dev/null || true
  for d in /etc/chrony/conf.d /etc/chrony.d; do
    [ -d "\$d" ] && printf 'server %s iburst\n' "\${NTP}" > "\$d/10-cluster.conf"
  done
  systemctl enable --now chronyd 2>/dev/null || systemctl enable --now chrony || true
  systemctl restart chronyd 2>/dev/null || systemctl restart chrony || true
else
  # systemd-timesyncd, which is what MicroOS ships. Drop-in, not a file edit:
  # /etc/systemd/timesyncd.conf is managed on some images and reverts.
  mkdir -p /etc/systemd/timesyncd.conf.d
  printf '[Time]\nNTP=%s\nFallbackNTP=\n' "\${NTP}" > /etc/systemd/timesyncd.conf.d/10-cluster.conf
  systemctl enable --now systemd-timesyncd || true
  systemctl restart systemd-timesyncd || true
  timedatectl set-ntp true || true
fi

# --- laptops: ignore the lid -------------------------------------------------
# Half of these machines have lids. A node that suspends when somebody closes
# it is not a node.
#
# A drop-in rather than an edit to logind.conf: on image-based and immutable
# systems the main file is vendor-managed and an in-place edit is reverted by
# an update, quietly, months later.
mkdir -p /etc/systemd/logind.conf.d
printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchDocked=ignore\nHandleLidSwitchExternalPower=ignore\n' \\
  > /etc/systemd/logind.conf.d/10-cluster-node.conf
systemctl restart systemd-logind

# --- did this machine end up with a pending snapshot? ------------------------
if [ "\${PKG}" = "transactional" ]; then
  echo "TRANSACTIONAL: packages are staged in a new snapshot and require a reboot."
fi
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
echo "--- time source (want ${NTP_SERVER}, not a public pool) ---"
${SSH} "chronyc -n sources 2>/dev/null || timedatectl show-timesync -p ServerAddress -p SystemNTPServers 2>/dev/null || true"
echo
echo "--- default route (want one; an address without a gateway passes a ping and nothing else) ---"
${SSH} "ip route | grep '^default' || echo 'NO DEFAULT ROUTE'"
echo
echo "--- iscsid (want 'active'; on a transactional system, after the reboot) ---"
${SSH} "systemctl is-active iscsid || true"
echo
echo "A reboot here is worth it: it proves swap stays off and the machine comes"
echo "back without you — and on a transactional system it is not optional, because"
echo "the packages above do not exist until it happens. Re-run this script after"
echo "the reboot to confirm; it is idempotent. Then join it — see README.md section 4."
