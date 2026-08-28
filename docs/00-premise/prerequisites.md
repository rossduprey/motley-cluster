# Prerequisites

**Complete this before `01-nodes/`.** Every item is here because skipping it cost somebody a
day, and most of them are much harder to do later than now — a decision like the internal domain
name propagates into certificates, DNS records and manifests within about an hour of first use.

---

## 1. Decide these, and write them down

These become the placeholders used throughout the repo. Fill in the right-hand column now; you
will paste from it constantly.

| Placeholder | What it is | How to decide |
|---|---|---|
| `<CLUSTER_DOMAIN>` | the internal DNS suffix every service lives under | **`home.arpa`** is reserved for exactly this by RFC 8375 and is what this repo assumes. A made-up TLD like `.lan` or `.home` works today and is squatting — it can collide with a real TLD later. If you own a domain, a subdomain of it is the other good answer. |
| `<LAN_SUBNET>` | your home network range | Read it off the router. Do not assume `192.168.1.0/24`. |
| `<CONTROL_PLANE>` / `<CONTROL_PLANE_IP>` | the machine running the API server | Wired, always on, nobody's daily driver, no lid. Not the fastest — the most *present*. |
| `<NODE_A>` … `<NODE_N>` | the workers | One row per machine in the inventory below. |
| `<ADMIN_USER>` | the account you SSH and `sudo` as, on every node | Make it the same on all of them. Different usernames per machine is a papercut that never stops. |
| `<ORG>` | the namespace your infrastructure repositories live under | Even if it is just your own name. |

**Choose the domain deliberately.** It is the single value with the widest blast radius: it is in
every certificate, every ingress rule, every DNS record and every URL anyone bookmarks. Changing
it after the fact is not a rename, it is a reissue of everything.

---

## 2. Take an inventory

One row per machine, before you install anything on any of them. This table is the input to
nearly every later decision — which node holds the control plane, what gets a storage disk, where
heavy workloads may run, and what will never fit.

| Machine | CPU / arch | RAM | System disk | Extra disk? | Wired or wifi | Currently holds anything? |
|---|---|---|---|---|---|---|
| | | | | | | |

**What each column decides:**

- **Architecture.** Mixed `amd64`/`arm64` is workable but means multi-architecture images or
  workloads pinned to the machines that can run them. Know before you are debugging an
  `exec format error`.
- **RAM.** This is the number the scheduler works from. A machine with a few GB is a legitimate
  worker; it is not a legitimate database host. Small nodes are real nodes — decide what each is
  *for* rather than which one you distrust.
- **Extra disk.** Determines which machines can hold storage replicas. Replicas do not go on the
  system disk (see item 5).
- **Wired or wifi.** Wifi nodes work and this cluster has them. They are the wrong home for
  anything latency-sensitive, for storage replicas, and for the control plane.

## 3. Ask what will be lost

Some of these machines have been doing something. **Ask before anything is reinstalled** —
photographs, a music library, someone's coursework, the only copy of a config file that took an
afternoon to get right.

This is one question and it is asked exactly once, at the point where the answer still matters.

## 4. Fix the addressing

Nodes need **stable IP addresses**. DHCP reservations are fine; DHCP roulette is not — a node
that changes address mid-cluster is its own afternoon, and the failure appears as unrelated
components losing each other.

Two specific traps, both of which we hit:

- **A reservation by MAC address may silently not apply.** Some DHCP clients identify themselves
  with a generated DUID rather than the MAC the router is matching on, and the node comes up on
  a different address than the one reserved for it, with no error anywhere. If a reservation does
  not take, look at the client's identifier setting before blaming the router.
- **Use the same network configuration stack on every node.** Different tools write
  `/etc/resolv.conf` differently — one writes a `search` line where another writes `domain` —
  and Kubernetes treats those two asymmetrically. This produced the single most misleading
  failure in `findings/what-did-not.md`, in which one node silently resolved internal service
  names to a completely unrelated machine.

**Verify, after each node is installed:** `cat /etc/resolv.conf` and confirm every node says the
same kind of thing.

## 5. Sort out the disks

For every machine that will hold storage replicas:

- A **dedicated disk**, not a directory on the system disk. External USB is fine.
- Mounted at a **consistent path across nodes**, by UUID in `/etc/fstab` so it survives a reboot
  and a re-plug. Device names are not stable; `/dev/sdb` today is `/dev/sdc` after somebody adds
  a drive.
- Formatted and empty.

And **one larger disk somewhere for backups**, which should not be one of the replica disks. Its
whole job is to be a copy that a mistake inside the storage layer cannot reach.

**Do the arithmetic now:** three replicas means every gigabyte a service stores consumes three
gigabytes of disk, spread across three machines. Size accordingly, and expect that the first
thing you run out of is disk on the node you thought was fine.

## 6. Passwordless SSH, from one place

From whatever machine you will be working on, key-based SSH to every node as `<ADMIN_USER>`,
with `sudo` available. Every install step and every diagnostic assumes it.

Do this before the first install rather than during it, because the moment you need it you are
already halfway through something.

## 7. Synchronise the clocks

Every node should be running NTP and agreeing about the time. This looks like housekeeping and
is not: certificate validity, token expiry, log correlation and lease-based leader election all
depend on it, and clock skew produces errors that name none of those things.

A laptop that has been switched off for six months is the usual offender.

## 8. Set up a password manager first

Before there is a cluster, there is somewhere to put its secrets. The encryption key for your
sealed secrets is the specific one that matters: **lose it and every secret you have committed
becomes permanently undecryptable**, including ones you have not created yet.

It belongs somewhere durable before the first secret is sealed. This is the one prerequisite that
cannot be retrofitted, because the retrofit is "generate new credentials for everything".

## 9. Have somewhere to write things down

Not optional, and it is layer zero of everything in `findings/`: a git repository for notes,
procedures and incidents, created before you have anything to write in it.

Documentation kept outside version control has no history and no recovery, and what it loses is
gone in the specific sense that no amount of effort brings it back.

---

## The gate

You are ready for `01-nodes/` when:

- [ ] Every placeholder in item 1 has a value written down.
- [ ] The inventory table is filled in, including RAM and wired-vs-wifi for every machine.
- [ ] Somebody has confirmed nothing on these machines is still needed.
- [ ] Every node has a fixed address, and you have verified the reservation actually applied.
- [ ] Replica disks are mounted by UUID at a consistent path, and a separate backup disk exists.
- [ ] Passwordless SSH works to every node, from the machine you will work from.
- [ ] Clocks agree.
- [ ] A password manager exists.
- [ ] A notes repository exists.

If one of these is unfinished, finish it here. Carrying an unresolved prerequisite forward is how
a weekend becomes a month.
