# motley-cluster

**A real Kubernetes cluster built from the laptops you were going to throw away — the full
runbook, and everything that went wrong.**

*motley* (adj.): made up of varied, mismatched parts. That is the premise, not an apology.
The machines are different ages, different architectures, different amounts of RAM, and some
of them are genuinely bad. The cluster works anyway. This repo is how.

---

## Status: early

This is being extracted from a cluster that has been running for months. The scaffolding and
the guard rails are in place; the content is landing layer by layer. **Nothing here is
aspirational — every document describes something that actually ran**, and anything not yet
written is simply absent rather than promised.

See [`docs/`](docs/) for what exists so far.

---

## What this is

A **foundation**. Not a curated set of apps — the platform underneath them:

- **k3s** across mismatched machines, with the scheduling work required to keep unequal nodes
  from tipping over
- **Real internal TLS** — a private CA, ACME, and certificates that renew themselves, so every
  service is `https://` and nothing warns
- **Replicated block storage** on consumer USB disks, with the failure modes that implies
- **Git, CI, and a container registry** running *inside* the cluster they build for
- **GitOps** — the cluster reconciles itself from a repo; you change a file, not a server
- **Observability** — metrics and logs, so "it's slow" becomes a number

Once that exists, running an application is a small, boring act. That is the point of it.

## What this is not

- **Not a homelab app list.** It is what you build *before* the app list is worth having.
- **Not production advice.** It is a home-scale cluster on salvaged hardware, and it is honest
  about where that breaks down.
- **Not a distribution or an installer.** There is no `curl | bash` that hands you a finished
  cluster. It is a path you walk, with the reasoning kept in.
- **Not somebody's dotfiles.** Every value you must supply is an obvious placeholder
  (see [Placeholders](#placeholders)). No hostname, address, or password from the original
  cluster appears anywhere — enforced by CI, not by good intentions.

## Who it is for

**Two readers, equally.**

**A human** who has a pile of old machines and wants real infrastructure practice rather than
a single-box appliance. You need to know why each piece is there, what it costs, what will
bite, and when to stop.

**An agent.** This repo is written to be handed to one:

> Drop this repo's URL into a coding-agent chat and say: *"Read this and help me build it on
> my hardware."*

The agent reads [`AGENTS.md`](AGENTS.md) first — read order, the invariants it must not break,
and how to verify each phase actually landed before moving to the next. Sequencing and
verification are the parts agents get wrong unprompted, so they are written down explicitly.

## The example cluster

Concrete, because vague hardware advice is useless — but it is **an example, not a
requirement**. The original build:

| | |
|---|---|
| **Nodes** | 5 machines — a mix of old laptops and outdated desktops |
| **RAM** | from ~3.7 GB to 16 GB per node. The small ones are real nodes, not decoration |
| **Storage** | consumer USB disks, replicated |
| **Network** | ordinary consumer gigabit, with some nodes on wifi |
| **Cost** | hardware that was otherwise going to be discarded |

If your machines are better than this, everything here still applies and will hurt less.

## Placeholders

Every value you must supply looks like this: `<NODE_A>`, `<CLUSTER_DOMAIN>`, `<ADMIN_USER>`,
`<LAN_SUBNET>`. They are deliberately loud. If you find something that looks like a real
hostname, address, or credential anywhere in this repo, **that is a bug** — please open an issue.

A CI job ([`scripts/check-anonymized.sh`](scripts/check-anonymized.sh)) fails the build on
private IPs, email addresses, key material, tokens, and internal hostnames. It is reusable if
you are publishing infrastructure docs of your own.

## Findings

The part that is hard to get anywhere else: **what did not work.**

Dead ends, incidents with the evidence attached, and measurements with the method stated.
Failed approaches are documented with the same care as successful ones, because knowing which
road is closed is worth as much as knowing which one is open.

See [`findings/`](findings/).

## License

**Not yet set.** Intended: permissive for the scripts and manifests, attribution-required for
the writing. Until a `LICENSE` file lands, no license is granted — treat it as all rights
reserved and check back rather than assuming.
