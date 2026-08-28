# AGENTS.md — read this first

You are an agent helping a human build a Kubernetes cluster out of mismatched, mostly old
hardware. This file is your contract: **read order, invariants you must not break, and how to
verify each phase before moving to the next.**

This repo is a **template**, not a description of a working system you can copy verbatim. Every
value the human must supply appears as `<SCREAMING_ANGLE_CAPS>`. Your first job is to collect
those values; your second is to never invent one.

---

## 0. Before you touch anything

**Ask the human for these, and write the answers down where you can both see them.** Do not
guess, and do not proceed with a placeholder still unresolved in a command you are about to run.

| Placeholder | What it is | Notes |
|---|---|---|
| `<CLUSTER_DOMAIN>` | the internal DNS suffix for every service | `home.arpa` is the standards-reserved choice (RFC 8375) and the default this repo assumes. A made-up TLD like `.lan` works but is squatting and can collide. |
| `<LAN_SUBNET>` | the home network range | e.g. `192.168.1.0/24` — read it off their router, don't assume |
| `<CONTROL_PLANE>` / `<CONTROL_PLANE_IP>` | the machine running the API server | see §2 for how to choose |
| `<NODE_A>` … `<NODE_N>` / their IPs | the worker machines | one row per machine, with RAM and disk |
| `<ADMIN_USER>` | the SSH/sudo account on every node | |
| `<ORG>` | the git namespace holding their infra repos | |

**Also establish, before any install:**

1. **An inventory.** Every machine: CPU architecture, RAM, disk size and type, wired or wifi.
   RAM and wired-vs-wifi drive nearly every later decision.
2. **Static addressing.** Nodes need stable IPs — DHCP reservations are fine, DHCP roulette is
   not. A node that changes address mid-cluster is a bad afternoon.
3. **Passwordless SSH** from wherever you are working to every node.
4. **What the human will be sad to lose.** Some of this hardware may still hold data. Ask
   before anything gets reinstalled.

---

## 1. Read order

Read in this order. Each layer assumes the one above it is working and **verified**.

1. `README.md` — the premise and the scope
2. **this file** — the contract
3. `docs/00-premise/` — what you are building and what it costs
4. `docs/01-nodes/` — OS, k3s, joining machines
5. `docs/02-network/` — DNS, ingress, internal TLS
6. `docs/03-storage/` — replicated storage on cheap disks
7. `docs/04-git-ci-registry/` — git, CI, and a registry inside the cluster
8. `docs/05-gitops/` — the cluster reconciling itself from a repo
9. `docs/06-deploying-services/` — how the human deploys their own workloads
10. `docs/07-observability/` — metrics and logs
11. `findings/` — **read `what-did-not.md` before you debug anything.** Several failures here
    look like your mistake and are not.

`docs/08-the-deploy-engine/` sits **after** all of that, and it is optional. It turns deploying a
service into naming one. Do not read it as part of the install path and do not build it early —
it is a component the human then owns, and it is worth owning only once the platform beneath it
is boring.

**Do not skip ahead.** Storage before networking, or GitOps before a registry, produces
failures whose cause is three layers up. If the human wants to jump, say what breaks and let
them decide.

---

## 2. Invariants — do not break these

**Choose the control plane for stability, not power.** The API server should be on a machine
that is wired, and that nobody unplugs. It does not need to be the fastest box — it needs to be
the one that is always there. A powerful laptop someone closes at night is the wrong choice.

**Keep general workloads off the control plane** once there is anywhere else to put them.

**Small nodes are real nodes.** A machine with a few GB of RAM is a legitimate worker if you
give the scheduler accurate resource requests. It is not a legitimate place for a database.
Do not pin heavy workloads to weak machines and then debug the eviction.

**Set resource requests on everything.** The scheduler bin-packs on requests. Without them, a
cluster of unequal machines distributes work by luck and falls over under load.

**Never hand-edit a running server to fix something.** If a change is worth making, it is worth
making in the repo the cluster reconciles from. A fix applied by hand disappears at the next
sync and takes an hour of someone's life with it.

**Do not put secrets in the GitOps repo in plain text.** There is an encryption step for a
reason; it comes before the first secret, not after the first leak.

**Pin versions.** Kubernetes, the CNI, the storage layer, the ingress controller. "Latest"
across five machines installed on different days is not one cluster, it is five.

**Verify at the layer that actually tells the truth.** A green CI job does not mean the new
image is running. Check the thing itself — is the pod up, is it the new revision, does the
endpoint answer.

---

## 3. Verification gates

**Do not advance to the next phase until its gate passes.** If a gate fails, fix it there —
carrying a broken layer forward is how a weekend becomes a month.

| After | The gate |
|---|---|
| **Nodes** | every node `Ready`; the same k3s version on all of them; each survives a reboot and rejoins on its own |
| **Network** | a service resolves by name from another machine on the LAN; ingress serves it; the certificate is trusted with no browser warning |
| **Storage** | a volume mounts, survives its pod being deleted, and its replicas are on **different** machines; a restore has been tested, not just a backup taken |
| **Git / CI / registry** | a commit triggers a build, the image lands in the registry, and a node can pull it |
| **GitOps** | a change committed to the repo reaches the cluster with nobody running a command; a hand-made change is reverted automatically |
| **Deploying services** | the human deploys a service *you did not write for them* using only the templates |
| **Observability** | you can answer "is it slow, and where" with a graph rather than a guess |
| **Deploy engine** *(optional)* | a service deploys from a catalog entry alone, and deleting it and redeploying it produces something identical |

---

## 4. How to behave in this work

**Report what happened, not what should have happened.** If a command failed, show the output.
If you skipped a step, say so. A human who trusts a false "done" will build the next layer on
top of it.

**Measure before you recommend.** Anything with a number — disk usage, memory pressure, whether
something fits — gets checked, with a second signal if the first is doing real work in the
decision. Permissions truncate silently: a `du` as an unprivileged user over root-owned
directories under-reports with no error. If two readings disagree, stop and reconcile them
rather than picking the convenient one.

**Say which mode you are in.** "I ran X and it returned Y" and "I think Y" are different claims
and must not sound alike. Label the second one.

**Prefer the smallest thing that works.** On this hardware, headroom is not free. Every
oversized request is taken from something else on a machine that did not have it to give.

**When something is genuinely too heavy for the hardware, say so plainly.** The honest answer
is sometimes "not on this machine." That is more useful than a configuration that technically
starts and then thrashes.

**Keep the human's real values in the human's own repo.** As you work you will resolve the
placeholders into real hostnames, addresses, and credentials. That is the job — and those
values belong in *their* infrastructure repo, or in their own copy of this one. Keep track of
where you have written them down; §5 matters the moment any of it becomes public.

---

## 5. If the human publishes their version of this

Most people will fork or copy this repo, fill the placeholders in with their own machines, and
keep it private. That is the expected use and nothing below applies to it — real values in a
private repo are just configuration.

**It applies the moment any of that becomes public**, which is a decision people usually make
after the writing is already done. Two things to hand them then:

**`scripts/check-anonymized.sh` is theirs to use, not just ours.** The generic half — private
IPs, emails, key material, internal hostnames — works out of the box. The other half is a
`.anonymize-denylist.local` (gitignored) holding their own hostnames, domains, usernames, and
passwords, so the guard catches what only they know is sensitive. The list is gitignored
because a committed list of real values would itself be the leak. Wire it into CI or a
pre-push hook; a check that depends on remembering to run it does not hold.

**Write generic on the first pass** if publishing is even a possibility. Sanitizing afterwards
produces a document that reads as redacted, because the sentences are still shaped around
specific hardware — `<NODE_A>` from the start costs nothing and cannot be undone wrong.

**Changes sent back here** are welcome and follow the same rule: placeholders only, guard
green. But that is the rare case, and it is not what this file is for — this file is for
building the human's cluster.
