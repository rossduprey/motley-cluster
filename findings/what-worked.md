# What worked

The other half of `what-did-not.md`. These are the choices that survived months of running on
hardware that was not designed for it — not because they were clever, but because they kept
being the thing that did not need revisiting.

**Every entry states its cost.** A recommendation without a cost is marketing. Some of these are
cheap and some of them are a permanent tax you agree to pay, and you should know which is which
before you adopt one.

Format for each: *what we chose* → *why it held* → *what it costs*.

Unlike `what-did-not.md`, this file **names the actual software**, because in most of these
entries the specific choice is the content. Substitute freely — but read the cost line before
you do, because the cost usually belongs to the *category*, not the product.

---

## Hardware and nodes

### Old laptops are real nodes, if you are honest about what they can hold

**What we chose.** Five mismatched machines — a mix of aging desktops and laptops, different
CPUs, RAM from a few GB to a useful amount, some with an internal disk only, some with external
USB disks bolted on.

**Why it held.** Kubernetes does not care that the machines are unequal. It cares that you told
it the truth about them. Resource requests on every workload, plus node labels expressing what
each machine is *for*, turned an unbalanced pile of hardware into a scheduler input rather than
a scheduler problem. The weakest node was given a real job — storage and file serving — rather
than being treated as the box nobody trusts.

**What it costs.** Discipline on requests, forever. Every workload deployed without them
degrades the whole arrangement, and the failure is gradual: things fit until they suddenly do
not, and the node that falls over is rarely the one at fault. Also: laptops have lids and
batteries. A machine somebody closes is not a server, whatever the CPU says.

### A boring machine for the control plane

**What we chose.** The API server went on the machine that is wired, uninteresting, and always
on — not the fastest one.

**Why it held.** Control-plane availability is the one thing whose loss makes every other
problem harder to diagnose, because the tooling you would diagnose with runs through it. A slow
API server is an annoyance; an absent one is an outage of your ability to see anything.

**What it costs.** You give up your best machine as a control plane and then have to actually
keep general workloads off it, which requires saying no more than once.

**And worth knowing:** moving the control plane later was possible, and was not a rebuild. It is
not a decision you are locked into — it is just a decision you would rather make once.

### k3s rather than upstream Kubernetes

**What we chose.** k3s, one pinned version, installed by a script that takes the server URL and
join token as arguments.

**Why it held.** A single binary with sane defaults, an ingress controller already present, and
a join that is one command. On this class of hardware the reduced resident footprint is not a
nicety, it is the difference between a node that runs workloads and a node that runs Kubernetes.

**What it costs.** Two real things:

- **Some components are k3s's, not yours.** The bundled ingress controller is managed through a
  k3s-specific config resource, not through your GitOps tooling, and you will look for it in the
  wrong place at least once.
- **Node-level configuration has no GitOps home.** Kubelet flags, reserved resources, taints
  that must survive reboot — these live in a file on each host. Nothing reconciles them. The
  best available answer is to keep a copy of each host's config in your infrastructure repo so
  the *intent* is tracked, and accept that applying it is a manual step. Say this out loud in
  your own docs; it is the one place the "everything is in git" story is not true.

**Pin the version.** Installed across five machines on five different days, "latest" is not one
cluster.

---

## Networking and certificates

### One internal DNS server, with a wildcard, that the deploy path writes to

**What we chose.** A single DNS server for the LAN, a wildcard record pointing the internal
domain at the ingress, and a per-service record written automatically as part of deploying a
service.

**Why it held.** A new service is reachable by name the moment it exists, with no separate step
anyone can forget. Making DNS part of the deploy path rather than a follow-up is most of why it
never drifted.

**What it costs.** A wildcard makes one specific failure much worse — a lookup that should have
missed instead returns a confident wrong answer. This is exactly the mechanism behind the worst
debugging session in `what-did-not.md`. It is still the right trade, but know that you have
armed it.

**Also:** the DNS server is a single point of failure for the whole house, not just the cluster.
When it is down, nothing resolves for anyone, including the people who did not sign up for this.

### An internal certificate authority, with ACME

**What we chose.** A small CA running inside the cluster, issuing to cert-manager over ACME,
with one cluster issuer that everything uses. No self-signed certificates anywhere, no manual
renewal.

**Why it held.** Internal HTTPS with no browser warnings, and certificates that renew without
anyone thinking about it. ACME against a local CA is the same well-tested code path as the
public one, so the failure modes are documented by someone other than you.

**What it costs.** **Trust distribution is a permanent chore.** Every machine that browses, and
every workload that makes an outbound HTTPS call to another internal service, needs the CA in
its trust store — and the list is longer than it looks. The failures are not obvious, because
they surface as `x509: certificate signed by unknown authority` in a component nobody suspected.
Injecting the CA into every generated workload from a single source solved most of it; see
`what-did-not.md` for what happens when that injection silently fails.

### A public jumpbox with a tunnel, rather than opening the home router

**What we chose.** A cheap VPS with a static address, a WireGuard tunnel from one node to it,
and a reverse proxy on the VPS terminating public traffic.

**Why it held.** Nothing on the home network is exposed directly. The public surface is one
small machine that can be rebuilt in an afternoon, and the home link's address can change
without anything breaking. It also works behind carrier-grade NAT, where port forwarding is not
available at all.

**What it costs.** A monthly bill, a second machine to keep patched, and a throughput ceiling:
every public byte crosses one tunnel, whose speed is bounded by the VPS's CPU doing encryption —
not by your home connection. Measure end to end from outside before quoting any figure about
public performance; the home link's speed is the wrong number.

---

## Storage

### Replicated block storage on cheap external disks

**What we chose.** A replicated storage layer (Longhorn), three replicas per volume, replicas
restricted to explicitly tagged external disks, with hard anti-affinity so no two replicas of a
volume share a machine.

**Why it held.** Consumer USB disks fail, and this arrangement means one failing is an event
rather than an incident. The anti-affinity is the load-bearing part: without it, "three replicas"
can quietly mean three copies on one machine, which is not redundancy, it is three chances to
lose everything at once.

**What it costs.**

- **Three times the disk, and real CPU.** Replication is synchronous. On weak nodes this is
  visible.
- **Placement must be expressed explicitly.** Disk tags, a storage-class disk selector, and
  scheduling turned *off* on every disk that is not meant to hold data. Intent that lives only
  in your head produces replicas on the OS disk — see `what-did-not.md`.
- **It is largely outside GitOps.** Node and disk configuration lives in the storage layer's own
  resources. Know which of your storage settings are reconciled and which are not.

### A backup target that is not the cluster

**What we chose.** A large disk on one node, exported over NFS, used as the backup target for
volume snapshots.

**Why it held.** It is off-volume and off-replica: a mistake inside the storage layer does not
take the backups with it, and restoring is a supported operation rather than an improvisation.

**What it costs.** It is still in the same building, on the same power, on the same LAN. It
protects against deletion, corruption and disk failure — not against fire or theft. If the data
matters, this is one layer of a plan, not the plan.

**Two non-negotiables** learned the expensive way, both documented in `what-did-not.md`: enrol
each volume in the backup schedule **in the same change that creates it**, and **restore once on
purpose** before you need to.

---

## Git, CI, and the registry

### Running git, CI, and the image registry inside the cluster

**What we chose.** A self-hosted git server, its CI runners, and a container registry — all on
the cluster they serve.

**Why it held.** The whole loop is local: push, build, push image, deploy, with no external
account, no rate limit, and no dependency on the house's internet connection being up. For a
project whose point is to understand the machinery, owning every link is the feature.

**What it costs.**

- **A bootstrap circularity you must plan for.** GitOps pulls manifests from the git server, so
  the git server cannot be deployed by GitOps. See the next entry.
- **You are the operator of a git server.** Its upgrades run database migrations; back up before
  major versions.
- **Version coupling between the CI server and its runners is real.** A server-side regression in
  the job-reporting API turned every green build red for us, with no clue in the build logs. If
  every run starts failing at the same point with no code change, suspect the pair, not your
  pipeline.
- **A registry inside the LAN can hit network quirks that a public one would not** — pushing very
  large images from a node back through the LAN to a service on that same node was unreliable
  for us. Pulling large payloads at runtime rather than baking them into images sidestepped it.

### A small, explicitly-listed bootstrap layer applied by hand

**What we chose.** Five manifests — the CA, the git server, the ingress configuration, and the
GitOps root application — are applied manually, once, on a fresh cluster. They are the documented
exception to "everything reconciles from git", and they are listed by name with the exact command
to update each one.

**Why it held.** The circular dependency is real and cannot be argued away, so the answer is to
make it **small, named, and boring** rather than pretending it does not exist. Every one of these
files is still in version control; only the *application* is manual.

**What it costs.** Two update paths to remember, and a category of change that will not appear in
your GitOps dashboard. Write the list down where people look, and keep it short — this is the one
place snowflakes are permitted, so it is the one place they accumulate.

### CI triggered by path, building only what changed

**What we chose.** One workflow per component, each triggered by a path filter on that
component's directory. Each builds an image, pushes it, and triggers the rollout itself.

**Why it held.** Commits touching documentation do not rebuild seven images. The mapping from
"directory I edited" to "thing that rebuilds" is obvious enough that nobody has to look it up.

**What it costs.** Everything is tagged `latest`, which makes rollback a rebuild rather than a
retag, and makes the **running pod's image digest the only ground truth** about what is
deployed. Green CI does not mean the new code is running; check the digest.

---

## GitOps

### Automated sync, self-heal, and prune — on from the start

**What we chose.** Every application syncs automatically, heals drift, and prunes removed
resources. Not "we'll turn that on when we trust it."

**Why it held.** It makes the repository true. A hand-edit to a running resource is reverted,
which is uncomfortable exactly once and then becomes the thing you rely on. Every serious
incident in `what-did-not.md` involving working "off the rails" happened in a corner these three
settings did not cover.

**What it costs.** You lose the ability to poke at a running system, and you will want to. The
tax is paid in the moments when the fast fix is a `kubectl edit` and the correct fix is a commit,
a push, and ninety seconds of waiting. Pay it anyway; the alternative is a cluster whose real
state nobody knows.

**And the caveat that bit us:** *Synced and Healthy* means the cluster matches the repository. It
does not mean anything is running — an application scaled to zero is perfectly healthy. GitOps
health and service health are different questions and need different instruments.

### An app-of-apps root with explicit sync waves

**What we chose.** One root application that watches a directory of application definitions, with
a wave number on each expressing ordering: certificate machinery first, then the registry, then
platform services, then everything else.

**Why it held.** Adding a service is adding a file. Ordering that would otherwise be enforced by
someone remembering to apply things in sequence is written down in the manifests instead.

**What it costs.** You have to actually think about the ordering, and wave numbers are a coarse
instrument — they express "after", not "when this is genuinely ready". Leave gaps between them.

### Encrypted secrets in git, from before the first secret

**What we chose.** Sealed secrets — encrypted with a cluster-held key, safe to commit, decrypted
only in the cluster.

**Why it held.** It means the "everything is in git" rule has no exception carved into it for the
most sensitive category of thing, which is where exceptions turn into leaks.

**What it costs.** **Key custody is now your problem, on day one.** The controller's private key
is the one piece of state whose loss makes every committed secret permanently undecryptable. It
belongs in a password manager before the first secret is sealed, not after. This is the
prerequisite you cannot retrofit.

---

## Deploying services

### A catalog: deploying a service is naming it

**What we chose.** A YAML catalog of every deployable service — image, template, resource
profile, node class, ordering. The deploy API takes a **name** and nothing else; everything else
is resolved from the catalog.

**Why it held.** Nobody has to remember, or re-derive, how a given service is supposed to be
configured. It is written down once in a file that a human, an agent, and the deploy engine all
read the same way. Adding a service is adding a catalog entry — data, not code, no rebuild.

**What it costs.** The catalog and its templates become a component in their own right, with
their own bugs and their own upgrades. And it concentrates risk: a defect in a template is
distributed to **every service generated from it**, arriving quietly and all at once. The
28-byte-placeholder-instead-of-a-CA-certificate failure in `what-did-not.md` is precisely this
shape.

### A small set of templates covering real shapes

**What we chose.** Roughly a dozen and a half templates — stateless, stateful, stateful with a
database, raw TCP, daemonset, cronjob, and a handful of genuinely special cases. Each generates
the full set of objects a service needs: namespace, resource limits, workload, service,
certificate, ingress route, and a PVC where relevant.

**Why it held.** Every service gets a certificate and an ingress route because the template
makes them, not because someone remembered. Consistency of this kind is what makes a cluster
navigable a month later.

**What it costs.** Templates diverge over time and the special cases multiply. Every so often the
honest move is to collapse two of them back together. Also, exactly one layer may own any given
value — when a template and a per-service override both set the same thing, the result was a
manifest that could not be applied at all, and no error anyone would notice.

---

## Observability

### Metrics, dashboards, and centralized logs — as one deployed stack

**What we chose.** Prometheus and Grafana from the standard bundle, plus a log aggregator with a
per-node shipper collecting both container output and host system journals.

**Why it held.** Centralized logs turned out to be worth more than metrics on a cluster this
size, for one specific reason: **they survive the pod.** Most of the incidents in this repo were
diagnosed by reading the logs of something that had already restarted, which is precisely the
case a live log tail cannot serve. Shipping host journals too — not just container output —
covered the failures that were below Kubernetes entirely.

**What it costs.** The monitoring stack is very often the largest resident consumer on a small
cluster, and it is the thing you will be tempted to trim when a node is under pressure. Budget
for it deliberately. Set retention low; you are not running an archive.

### Probing services from the outside, on the path a user takes

**What we chose.** A blackbox prober hitting real service URLs and alerting on sustained failure.

**Why it held.** It is the only signal in the whole stack that answers "does this actually work",
as opposed to "is a process running and does the repository match". A service was `1/1 Ready` for
five days while returning an error to every request; this is the instrument that would have
caught it in minutes.

**What it costs.** Our target list is static, so a service deployed later is not probed until
someone adds it — a monitoring system that depends on you remembering has a half-life. Generate
the list from whatever your source of truth for running services is.

**And the prerequisite nobody enjoys:** alerting is worthless without a delivery channel that
reaches a human who is not looking at a dashboard. Wire that up when you wire up the alerts, not
later.

---

## Practices that paid for themselves

### One operations document, read at the start of every session

A single reference — nodes and their roles, which repository owns what, what is reconciled and
what is applied by hand, the upgrade path for each *class* of component, the mistakes that
recur — read before touching anything.

**Why it held.** It is the difference between knowing where a change goes and guessing. Most of
its value is in one section: the list of things that are **not** managed the usual way.

**What it costs.** It is only true if updating it is part of finishing work rather than a
separate task. Work out a pattern the document lacks, and adding it back is the last step of the
job.

### Writing up failures at the time, with the evidence attached

Each significant incident got a document: symptom, investigation, root cause, fix, and the
command output as it actually appeared.

**Why it held.** Everything in `what-did-not.md` came out of those write-ups. Reconstructing them
from memory months later would have produced a much vaguer and much less useful file — the
narrative is easy to recall and the **evidence is not**, and the evidence is the expensive half.

**What it costs.** Twenty minutes at the end of an incident, at the exact moment you want to stop
thinking about it. That is the whole cost, and it is the highest-return twenty minutes in this
entire document.

### Documentation in version control, and searchable

Notes, procedures, and reference material live in a git repository like everything else, indexed
into a search engine so a question can be answered in one query instead of by grepping a laptop.

**Why it held.** Version control gives documentation the same properties it gives code: history,
recovery, and a review point. Search is what makes people actually consult it rather than
re-deriving.

**What it costs.** Nearly nothing, and the alternative is not cheap — documentation kept outside
version control has no history and no recovery, and what it loses is gone in the specific sense
that no amount of effort brings it back.

---

## The pattern underneath these

`what-did-not.md` ends on a single recurring shape — *the system reported success, and the report
was true but irrelevant*. This file has one too, and it is the mirror image:

**The choices that held were the ones that made the correct thing the automatic thing.**

DNS records written by the deploy path, not by a person. Certificates issued by a template, not
requested. Drift reverted by a controller, not noticed. Images rebuilt by a path filter, not by
remembering which directory maps to which pipeline. In every case the mechanism is the same:
the work was moved from *someone's memory* into *something that runs*.

The entries that still bite are precisely the ones where that move was never made — a static
probe list, a backup enrolment done by hand, a node configuration file no controller reads. Each
of those works fine on the day you set it up, and each has a half-life measured in how long you
keep caring.

**So when you are deciding whether something is worth automating on a five-machine cluster: the
question is not how much time the automation saves. It is whether the manual version will still
be happening in three months.**
