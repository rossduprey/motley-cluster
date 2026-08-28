# 01 — Nodes

**Turning a pile of mismatched machines into a cluster.**

By the end of this section every machine is running the same pinned version of k3s, every node is
`Ready`, and each one survives a reboot and rejoins on its own. Nothing else in this repo works
until that is true.

**Prerequisite:** the checklist in [`../00-premise/prerequisites.md`](../00-premise/prerequisites.md)
is complete. In particular the inventory exists, the addresses are fixed, and passwordless SSH
works — the steps below assume all three.

---

## The shape of this layer

```mermaid
graph LR
    P["<b>prepare</b><br/>every machine, identically"] --> S["<b>server</b><br/>control plane first"]
    S --> J["<b>join</b><br/>one worker at a time"]
    J --> L["<b>label</b><br/>say what each machine is for"]
    L --> V["<b>verify</b><br/>reboot each one"]

    style P fill:#1e293b,stroke:#38bdf8,color:#e2e8f0
    style S fill:#1e293b,stroke:#f59e0b,color:#e2e8f0
    style J fill:#1e293b,stroke:#38bdf8,color:#e2e8f0
    style L fill:#1e293b,stroke:#38bdf8,color:#e2e8f0
    style V fill:#1e293b,stroke:#22c55e,color:#e2e8f0
```

**One machine at a time, all the way through.** Preparing five machines and then joining five is
how you end up debugging five broken nodes at once instead of one.

---

## 1. The operating system

**A minimal, current, 64-bit Linux with no desktop environment.** This repo was built on a
Debian-family distribution and the commands below are `apt`-flavoured; nothing about the design
depends on that choice, but *making the same choice on every machine* matters more than which
choice you make.

Two things to get right at install time, because they are annoying afterwards:

- **No desktop.** A graphical session on a node is RAM you have taken from workloads, on machines
  that do not have it to give.
- **The same major version everywhere.** Different kernel and systemd generations across nodes
  turn "works on one machine, not the other" into a routine sentence.

If a machine can only run a 32-bit OS, it is not a node. That is the one hard hardware floor.

## 2. Prepare every machine identically

[`prepare-node.sh`](prepare-node.sh) does everything in this section. Read it before running it —
it is short, and it is a template you are expected to edit.

What it does and why each step is there:

| Step | Why |
|---|---|
| **Passwordless `sudo`** for `<ADMIN_USER>` | Every later step is remote and non-interactive. Without it, automation stalls on a password prompt you cannot see. |
| **Disable swap**, and remove it from `/etc/fstab` | The kubelet expects it off. Removing it from `fstab` too is what makes it stay off after a reboot. |
| **Install and enable `open-iscsi`**, load the iSCSI kernel modules | Required by the replicated storage layer in `03-storage/`. Doing it now costs one line; discovering it later costs an afternoon of volumes that will not attach. |
| **Set the hostname** | Node names in Kubernetes come from here, and they are not easy to change later. |
| **Ignore the lid switch** | Half of these machines are laptops. A node that suspends when someone closes it is not a node. |
| **Install the SSH public key** | So the rest of the build is key-based and scriptable. |

**Three network settings that are not optional**, each of which caused a real incident:

- **Use one network configuration stack across all nodes** — and check what it writes into
  `/etc/resolv.conf`. A `search <domain>` line is propagated by the kubelet into every pod on
  that node; a `domain <domain>` line is ignored. Combined with a wildcard DNS record for your
  internal domain, the first form makes every in-cluster service lookup on that node resolve to
  the wrong machine, silently. This is the worst debugging session in
  [`findings/what-did-not.md`](../../findings/what-did-not.md), and it presents as a single
  application crash-looping on a single node.
- **Make the DHCP client identify by MAC**, not by a generated DUID, or the router's reservation
  will not apply and the node comes up on an address nobody expected.
- **NTP running**, clocks agreeing. Certificates and tokens fail in ways that name neither.

**Verify before moving on**, on each machine:

```bash
cat /etc/resolv.conf      # every node should say the same kind of thing
free -h                   # swap total: 0
systemctl is-active iscsid
timedatectl               # synchronized: yes
```

## 3. Install the control plane

**On `<CONTROL_PLANE>` only**, and before any worker exists.

Write `/etc/rancher/k3s/config.yaml` *first*, then install — the installer reads it:

```yaml
# /etc/rancher/k3s/config.yaml on <CONTROL_PLANE>
node-ip: <CONTROL_PLANE_IP>
tls-san:
  - <CONTROL_PLANE_IP>
  - <CONTROL_PLANE>.<CLUSTER_DOMAIN>
```

`tls-san` is the one people omit and regret: it is the list of names and addresses the API
server's own certificate is valid for. Add every name you might ever use to reach it, including
the DNS name you have not set up yet. Adding one later means reissuing the certificate.

Then install, **with the version pinned**:

```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=<K3S_VERSION> sh -
```

Pick a specific version and write it down; it is now a value in the same category as
`<CLUSTER_DOMAIN>`. `latest`, installed across five machines on five different days, is not one
cluster.

**Two things you need from this machine afterwards:**

```bash
sudo cat /var/lib/rancher/k3s/server/node-token   # the join token — treat it as a credential
sudo cat /etc/rancher/k3s/k3s.yaml                # the kubeconfig; edit its server address
```

Copy the kubeconfig to your workstation and replace `127.0.0.1` in it with `<CONTROL_PLANE_IP>`.
Working from your own machine rather than SSH-ing to the server for every command is worth the
five minutes.

> ### ⚠️ This file has no GitOps home, and neither does anything like it
>
> `/etc/rancher/k3s/config.yaml` is host-local. Nothing reconciles it. Kubelet flags, reserved
> resources, and taints that must survive a reboot all live here, and none of them are reachable
> by the GitOps machinery you will build in `05-gitops/`. **This is the one place the
> "everything is in git" rule of this repo is not true.**
>
> The best available answer, and the one this cluster settled on: **keep a copy of each host's
> config in your infrastructure repository** — `node-config/<hostname>-config.yaml` — so the
> intended state is tracked and reviewable even though nothing applies it automatically.
> Applying a change is `ssh` plus a service restart. Write this convention down in your own docs
> now, because the moment you forget it exists, the hand-edited version on one host becomes the
> only copy of an important decision.
>
> k3s also reads every fragment in `/etc/rancher/k3s/config.yaml.d/`, which is a tidy way to keep
> one concern per file.

## 4. Join the workers, one at a time

On each worker, after preparation:

```bash
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_VERSION=<K3S_VERSION> \
  K3S_URL=https://<CONTROL_PLANE_IP>:6443 \
  K3S_TOKEN=<JOIN_TOKEN> \
  sh -
```

Same version string as the server. **Note what this does not do:** the agent installer takes the
URL and token as environment variables and does *not* write them into a config file. There is
therefore no on-disk record on the worker of what it joined or how — which is another reason the
copy in your infra repo matters.

Then, from your workstation:

```bash
kubectl get nodes -o wide
```

Wait for `Ready` before starting the next machine. A node that joins and then goes `NotReady` is
usually swap, iSCSI modules, or the clock — in that order.

**A guard worth copying** from this cluster's onboarding script: before touching a machine,
`ssh` to the address and confirm the hostname it reports is the one you expect, and abort if it
is not. Preparing the wrong machine — a laptop that took the address you meant for the desktop —
is a genuinely bad afternoon, and this is a three-line check.

## 5. Say what each machine is for

Kubernetes distributes work by luck unless you tell it two things.

**Labels — the machine's role:**

```bash
kubectl label node <NODE_A> role=heavy      # the box with real RAM
kubectl label node <NODE_B> role=edge       # the one with the storage disks
```

Give the weak machine a *purpose* rather than a warning label. A node treated as "the one we do
not trust" is a node whose capacity you have thrown away; a node designated for DNS, or for file
serving, is a node doing a real job that happens to be light.

**Resource requests — on every workload, forever.** The scheduler bin-packs on requests. Without
them, a cluster of unequal machines distributes work by chance and falls over under load, and the
node that falls over is rarely the one at fault. This is not a step you do here; it is a rule you
adopt here and never stop applying.

**Keep general workloads off the control plane** as soon as there is anywhere else to put them.
A taint is the enforceable version of this, and it belongs in the host config file from §3 so it
survives a reboot — a `kubectl taint` applied by hand does not.

## 6. Two things about wifi nodes

They work. This cluster has them. But:

- **Nothing latency-sensitive, no storage replicas, and never the control plane.**
- A wifi node that drops off the network for thirty seconds is a node whose pods get rescheduled
  elsewhere and whose volumes have to detach and reattach. It is not a failure, but it is churn,
  and it is worth labelling the node so that nothing important lands there by accident.

## 7. Removing a node

**Delete dead nodes. Do not simply leave them.**

```bash
kubectl drain <NODE> --ignore-daemonsets --delete-emptydir-data
kubectl delete node <NODE>
```

A `NotReady` node that is left alone keeps holding pod references that look perfectly healthy in
the API and are running nowhere. On this cluster that stranded ten pods — including certificate
challenge pods, which do not belong to a controller and are therefore never rescheduled — and it
cost two expired certificates three days later, in a component nobody would have connected to it.
The full story is in [`findings/what-did-not.md`](../../findings/what-did-not.md).

---

## Coming back to this layer later

Two steps belong to this section but cannot be done yet, because they depend on things that do
not exist until `02-network/` and `04-git-ci-registry/`. Note them now so they are not a surprise:

- **Trust for your internal CA** must be installed on every node, and again on every node you add
  after that.
- **A registry mirror configuration** (`/etc/rancher/k3s/registries.yaml`, pointing at your
  in-cluster registry and referencing the CA file) must exist on every node, or pods pulling
  your own images sit in `ImagePullBackOff`. The node onboarding script does not write it.

**Both of these make "adding a node" a longer procedure than it is today.** Keep the onboarding
script in your infra repo and extend it as each layer lands, so that adding the sixth machine in
six months is still one command rather than an archaeology exercise.

---

## The gate

Do not start `02-network/` until all of this is true:

- [ ] `kubectl get nodes` shows every machine `Ready`.
- [ ] Every node reports the **same k3s version** (`kubectl get nodes -o wide`).
- [ ] **Each node has been rebooted** and rejoined on its own, without intervention.
- [ ] `swap` is off everywhere and stays off across that reboot.
- [ ] `/etc/resolv.conf` is consistent across all nodes, and you have looked at it yourself.
- [ ] Clocks agree.
- [ ] Node labels are applied, and the control plane is either tainted or deliberately not.
- [ ] The control plane's `config.yaml` has a copy in your infrastructure repository.

The reboot test is the one people skip. It is also the only one that proves the cluster survives
a power cut, which is the event most likely to happen to a cluster living in someone's house.
