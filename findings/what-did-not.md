# What did not work

Every entry below is something that actually happened on the example cluster. They are written
here because each one cost real time, and most of them **look like a different problem than they
are** — which is the expensive part.

**Read this before you debug anything.** Several of these present as a broken application when
the cause is three layers down, and you will otherwise spend a day inside the wrong component.

Format for each: *what we did* → *what happened* → *why* → *what to do instead*. Where a failure
was silent, that is stated explicitly, because a silent failure is a different class of problem
from a loud one.

---

## Storage

### A backup job with no volumes enrolled looks exactly like a working backup job

**What we did.** Configured daily and weekly recurring backup jobs against a remote target,
confirmed the target was reachable, and moved on.

**What happened.** The jobs ran on schedule for **74 days and backed up nothing.** Volumes must
be *enrolled* in a recurring job individually; creating the job does not enrol anything. Nobody
noticed, because a job that backs up zero volumes succeeds. It was discovered during a real data
loss, which is the worst possible time to discover it.

**Why it stayed invisible.** There was no alert on "backup job completed with zero volumes",
and the dashboard showed green. The monitoring stack was **not scraping the storage layer at
all**, so every storage alert rule that existed was dormant — configured, evaluated against no
data, and therefore permanently silent.

**Instead:**

- Enrol a volume in the backup job **at deploy time**, in the same change that creates it. Not
  in a cleanup pass.
- Verify the metrics endpoint of the storage layer is actually being scraped. An alert rule with
  no underlying metric is not a safety net, it is the *appearance* of one.
- **Restore once, on purpose, before you need to.** A backup you have never restored is a
  hypothesis.

### The snapshot existed and was useless — retention shorter than time-to-detection

**What happened.** A database's schema was wiped. The loss went unnoticed for **five days**,
because the application kept answering and the pod stayed `Ready`. By the time anyone looked,
the only snapshot available had been taken *after* the data was already gone. It restored
cleanly and produced an empty database.

**Why.** Retention was seven days, which sounds generous until you subtract five days of
undetected loss. And a snapshot taken after the damage is a faithful copy of the damage.

**Instead:** size retention against **how long a problem can plausibly go unnoticed**, not
against how often you take snapshots. If nothing tells you within a day that a service is
broken, a seven-day window is really a two-day window.

### Detaching a volume through the storage UI, outside the CSI path

**What we did.** Used the storage system's own web UI to detach a volume that Kubernetes was
managing — it was right there, and it looked like the direct route.

**What happened.** It left a stale iSCSI target registered on the node with no matching
initiator. Every subsequent attach failed with *"this logical unit is still active"*, and the
workload could not start at all. The fix was restarting the storage instance-manager pod on that
node.

**Why.** The CSI driver owns the attach/detach lifecycle. Reaching around it leaves the
kernel-side state and the controller-side state disagreeing, and the controller has no way to
know.

**Instead:** never attach or detach a Kubernetes-managed volume by hand. Drive it through the
workload — scale down, let CSI detach; scale up, let CSI attach. This came up **twice** in
different incidents, the second time after it had already been written down.

### A filesystem error flag silently blocks online resize forever

**What we did.** Expanded a PVC that had filled up.

**What happened.** The block device grew. The filesystem did not. The PVC sat in
`FileSystemResizePending` and emitted **500+ resize-failure events**, and the error was
`Permission denied` — which sends you looking at RBAC, mount options, and privilege, none of
which were the problem.

**Why.** The disk had filled, writes had failed, and that set the error flag in the ext4
superblock. `resize2fs` deliberately refuses to resize a filesystem carrying that flag. `EPERM`
is a badly chosen errno for "I am refusing on principle", and it cost the whole diagnosis.

**Instead:** check the filesystem state (`tune2fs -l` → *"clean with errors"*) before believing
a permissions story. Run `e2fsck -fy` on the unmounted device to clear the flag, then resize.
`e2fsck` exit code 1 means "errors corrected" and is a **success** here.

**And the structural fix:** a volume that fills up does not merely stop accepting writes, it can
damage the filesystem badly enough to block the obvious remedy. Alert on volume *fullness*, not
on volume failure.

### Read-write-once volume plus a hard node pin equals "deployed, but no pod, and no error"

**What happened.** A workload was pinned to a specific machine with `nodeSelector` and used an
RWO volume. The volume was attached elsewhere — or detached and not reattaching — so the pod
could never be scheduled. From the outside: the deployment existed, the app showed as deployed,
and nothing at all happened. No disk activity, no network activity, no logs, for days.

**Instead:** treat "deployed but zero pods and zero events" as a **volume attachment** question
first. And avoid pinning a workload to one node unless something physical requires it — a node
pin converts a recoverable scheduling problem into a hard stop.

### Replicas quietly piled onto the wrong disk

**What we did.** Added large external disks to some nodes intending them to hold bulk replica
data, leaving the small internal system disks for the OS.

**What happened.** One node's internal system disk was still marked schedulable and had
accumulated **50 replicas**, while the large disk beside it held three. Nothing was broken;
everything was in the wrong place, and the system disk was quietly filling.

**Why.** Placement had no disk tags, no node tags, and no storage-class disk selector. The only
lever actually in use was per-disk "allow scheduling". Default placement then does the sensible
thing with the information it has, which was not the intent nobody had encoded.

**Instead:** if a disk is not meant to hold data, **turn its scheduling off explicitly.** Intent
that lives only in someone's head is not configuration. Then check where replicas actually
landed rather than where you meant them to land.

### Disk records outlive disks

Two variants, both of which take capacity out of the pool without an obvious error:

- **A phantom record with an empty path**, left over from a disk add/remove, permanently
  `NotReady` with `failed to get fs stat for ""`. Harmless but permanently red, which trains you
  to ignore red.
- **A filesystem UUID mismatch** after a disk was reformatted or remounted — the storage layer
  refuses the disk *to protect data*, reporting zero capacity while still showing a phantom
  reservation. Correct behaviour, alarming presentation.

**Instead:** removing a disk record often requires disabling scheduling on it **first** — the
admission webhook rejected the removal until we did, with a 500 rather than a useful message.
After a remove-and-re-add, confirm the disk returns `Ready` *and* `Schedulable` with a plausible
capacity. If it comes back with the same mismatch, the problem is on the host — the mount, or
the on-disk config file — and no amount of API work will fix it.

---

## DNS and networking

### One node running a different network manager silently corrupted DNS for every pod on it

This is the single best example in this repo of a failure that looks like something else
entirely.

**Symptom.** A registry component crash-looped — but *only when scheduled onto one particular
machine*. Healthy everywhere else. Its liveness probe timed out; its logs showed it failing to
reach its own cache service.

**Actual cause, three layers down.** That one node ran NetworkManager; every other node ran
dhcpcd. NetworkManager writes `search <domain>` into `/etc/resolv.conf`. dhcpcd writes
`domain <domain>`. **kubelet propagates `search` entries into every pod's DNS config and ignores
`domain` entries.** With Kubernetes' default `ndots:5`, a cluster-internal name like
`service.namespace.svc.cluster.local` — four dots — gets the search domain appended and tried
*first*. That expanded name matched the LAN's own wildcard DNS record and resolved to a
completely unrelated machine. Every in-cluster service call from pods on that node went to the
wrong host, and the absolute lookup was never attempted.

**Instead:**

- After adding any node, check `/etc/resolv.conf` and confirm it says `domain`, not `search`.
  This is a one-line check that would have saved the entire investigation.
- Standardise the network manager across nodes. A heterogeneous cluster is fine; a
  heterogeneous *resolver configuration* is not.
- A wildcard DNS rewrite for your internal domain is convenient and makes this failure mode much
  worse — it turns a lookup miss into a confident wrong answer.

**Related, same node, same session:** DHCP reservations by MAC silently did not apply, because
dhcpcd defaults to `duid` identification and the router was matching on MAC. The node came up on
a different address than the one reserved for it. Setting `clientid` in `dhcpcd.conf` fixed it.
A node that changes address mid-cluster is its own afternoon.

### DNS was enforced with a firewall rule; NTP was left to configuration, and drifted for months

**What we did.** Ran a filtering resolver on the router for the whole household, and — correctly —
did not trust every device to point at it. A redirect rule on the router caught anything sent to
port 53 and forced it into the resolver regardless of what the client had configured. That control
worked, and it worked for long enough that we stopped thinking about it.

Time was handled the other way. Nodes were left with whatever their distribution shipped, which is
a public NTP pool.

**What happened.** Nothing, visibly, which is the finding. Every node in the cluster spent months
synchronising its clock against an internet pool, directly, while we believed the network's egress
was controlled — because the one control we *had* built was real, and its existence was doing the
reassuring. It surfaced only when a second network segment was built beside the first and the same
question was asked from scratch: `timedatectl show-timesync` on a node named a public pool address,
and `netstat -lnup` on the router showed nothing listening on 123 at all. There had never been a
local time source to point at.

**Why it is worth its own entry.** The two protocols are not different in importance — certificate
validity, token expiry and lease-based leader election all sit on the clock — they were different
only in that one had a rule and the other had an intention. A control that exists for one protocol
creates a *category* impression: "our egress is handled." Nobody audits the members of a category
they believe is handled.

**What to do instead.**

- **Serve time locally, from the gateway or another machine you control**, and point nodes at it.
  A policy with no local destination is not a policy; it is a preference that loses to the default.
- **Enforce it the same way you enforced DNS** — reject outbound 123 from the segment, so a node
  that is reconfigured fails visibly rather than drifting onto someone else's clock. Match the
  enforcement to the *policy*, not to the protocol you happened to think about first.
- **When you build a control, write down what it does not cover.** The redirect rule covered DNS.
  Nothing recorded that it covered only DNS, so it read as "egress is controlled".
- **Check parity whenever you add a segment.** A rule scoped to one zone silently does not apply
  to the next one you create. This is how the gap was found, and it is worth doing deliberately
  rather than by luck: list the rules on the old segment, and confirm each has a counterpart.

**Stated honestly, as a limit:** none of this touches DNS-over-HTTPS, which rides 443 and is not
separable from ordinary web traffic by port. Blocklists of known DoH endpoints are mitigation, not
enforcement. Port-based rules are worth having and are not the same as control.

---

## Certificates

### Certificates expired because their challenge pods were stranded on a dead node

**What happened.** A node was created out-of-band, ran for about eight hours, went `NotReady`,
and was never deleted. ACME HTTP-01 challenge solver pods for two certificate renewals had been
scheduled onto it while it was healthy. They stuck in `ContainerCreating` forever. Both
certificates expired.

**Why the pods were never rescheduled.** Solver pods are bare, short-lived pods — not owned by a
controller that handles eviction. The node-lifecycle controller did not move them. Ten orphaned
pods in total were still pinned to that dead node, including storage and monitoring daemons,
all looking healthy in the API and none of them running.

**And then it got worse on its own.** cert-manager records `lastFailureTime` and backs off
roughly two hours per failure. Cleaning up the dead node was not enough; the backoff had to be
cleared explicitly by patching the certificate's status subresource, or issuance would keep
waiting.

**Instead:**

- **Delete dead nodes.** A `NotReady` node that is merely left alone keeps holding pod
  references that appear fine in the API and are running nowhere.
- When cert-manager is stuck, look for `Backing off from issuance due to previously failed
  issuance(s)` and clear `lastFailureTime` after fixing the underlying cause.
- Alert on certificate *expiry approaching*, not on renewal failure. Renewal had been failing
  quietly for days.

**A syntax trap that cost time inside the cleanup:** deleting several resource types in one
command using the space-separated form processes only the **first** type and exits successfully.
Use the `type/name type/name` slash form. It fails silently, which is how it survives review.

### Everything trusts the internal CA — except the things you forgot

An internal certificate authority is the right answer for a LAN, and this repo recommends it.
The recurring cost is that trust must be installed *everywhere something makes an outbound
HTTPS call*, and the list is longer than it looks.

**Concretely:** the vulnerability scanner's per-image scan jobs could not pull image metadata
from the internal registry — `x509: certificate signed by unknown authority` — so scanning was
broken for essentially every workload the cluster actually ran, while the scanner's own
Application reported **Synced and Healthy** and its dashboard held plenty of reports for
*public* images. It looked like it was working. It was working on the wrong half of the estate.

**The dead end we went down:** reaching for "insecure registry" flags first. One of them turned
out to map to a different setting than its name implied, and the one that *was* correct only
affected a different component's own registry access, not the scan jobs' image fetches. Two
rounds of that.

**Instead:** inject the CA certificate properly — most charts have a values key for exactly this
— rather than disabling verification. Skipping verification is not only worse, in this case it
also **did not work**, which is the ideal outcome for a shortcut.

---

## Controllers and scheduling

### A Deployment pinned to one node with a host port generated 106 dead pods

**What we did.** Ran a DNS server as a `Deployment` with `strategy: Recreate`, `nodeName` set
directly to one machine, and a `hostPort`.

**What happened.** Something — never identified — nudged the controller into recreating the pod.
The single healthy pod already held the host port, so the scheduler rejected every replacement:
`didn't have free ports for the requested pod ports`. The controller tried again. And again.
**106 `Failed` pods accumulated.** Service was never interrupted; the original pod served DNS
throughout.

**Why.** `Deployment` + `nodeName` produces a controller that will retry forever against a
constraint it cannot satisfy, and `strategy: Recreate` makes it especially eager. Kubernetes
does **not** garbage-collect pods in the `Failed` phase, so the tombstones simply pile up.

**Instead:** for anything using a `hostPort` and pinned to a specific machine, use a
**DaemonSet** with a `nodeSelector`. The DaemonSet controller understands one-pod-per-node and
does not spam replacements when admission rejects one.

**Worth internalising separately:** a namespace with 107 pods in it looked like an emergency and
was not. Pod *count* is not health. Check phases before reacting.

---

## Health, and knowing whether anything actually works

### `1/1 Ready` for five days while every request returned 503

The forum incident above is really two failures, and this is the second one. The pod was
running. Its liveness probe passed — the probe checked that a process was listening, which it
was. The application behind it was answering every single request with an error, because its
database was empty.

**Nothing in the cluster noticed.** No alert fired. It was found by a person visiting the site.

**Instead:** probe from the outside, on the path a user actually takes. A blackbox exporter
hitting real service URLs and alerting after a few minutes of failure is a small amount of
configuration and is the difference between five days and five minutes.

**The known weakness of the fix we shipped:** the probe target list is static, so a service
added later is not probed until someone remembers to add it. A monitoring system that requires
you to remember is a monitoring system with a half-life. Generate the list from whatever your
source of truth for running services is.

### Green CI does not mean the new code is running

A pipeline that builds and pushes an image successfully tells you the image exists. It says
nothing about whether anything pulled it. Check the **running pod's image digest**, and compare
it across two pods pulled at different times if it matters.

### An advisory status field read as an authoritative one

An in-house deploy tool tracked each job's progress in a background thread, polling until the
job reached a terminal state. When the tool's own pod restarted, every in-flight thread died and
the job records froze at whatever they last said. Jobs that had long since succeeded reported
`committed` forever.

Nothing was actually broken — the cluster was correct the whole time. But for a while we were
debugging deploys based on a status display, which was the wrong source of truth.

**Instead:** know which of your status surfaces are **derived** and which are **authoritative**.
The cluster's own state is authoritative. Anything caching a view of it is advisory, and should
say so on its face. If you write such a tool, reconcile in-flight records on startup.

---

## Working outside the rails

Every incident in this section shares a root cause: someone applied something to the cluster
directly instead of committing it to the repository the cluster reconciles from. Including us,
knowing better, with the rule already written down.

### One out-of-band `kubectl apply` cost two expired certificates

The dead node that stranded the ACME solvers above got there by a direct apply — a nested test
cluster, created out of band, that registered as a real node. It ran, it died, and the damage
surfaced **three days later** in a component nobody would connect to it.

**This is the shape to remember:** an off-rails change does not usually break the thing you
touched. It breaks something else, later, in a way that gives no hint where to look.

### Hand-applied workloads become permanently invisible

A workload applied by hand is unknown to GitOps and to any deploy tooling you have. It will not
be reconciled, will not be evicted, will not be cleaned up, and will not appear in inventories.
One such workload sat crash-looping — **36 restarts in ten hours** — and its committed manifests
had already drifted from what was actually running: the live pod was on a different machine than
its own README claimed, and mounted a volume that had been hand-created and did not appear in
the manifests at all.

**If you must do a one-off**, write down at the same moment: that it exists, that it is off the
rails, and how it gets cleaned up. Then put a date on converting it. A snowflake with no expiry
date is permanent.

### `Synced + Healthy` is not the same as "running"

An app scaled to `replicas: 0` is a perfectly valid state, so GitOps reports it **Healthy** and
green. Two services sat scaled to zero — reason unknown, most likely a manual edit in some
earlier session — and the dashboard was entirely content about it.

**Instead:** health dashboards answer "does the cluster match the repo". They do not answer "is
anything running". Those need separate signals — which is the blackbox probing point again, from
a different direction.

### Anything that predates your tooling needs an explicit adoption step

Services deployed before a deploy tool existed had no records in it. Its restore operation
returned `404` for them, because there was nothing to restore *from* — a diagnostic that reads
like "not found" and actually means "never known about". Adopting them meant creating the
records the tool expected by hand, once, and then cycling each service through the tool to prove
the path worked end to end.

**Instead:** when you introduce tooling that owns a lifecycle, inventory what already exists and
adopt it deliberately. And flag adopted records as adopted — you will want to tell them apart
later.

### An interrupted multi-step operation leaves a state nothing can describe

An evict operation was supposed to write an archive, strip the manifest, then flip a registry
flag. It got partway: the manifest carried an `# EVICTED` header, the registry said `evicted`,
and the service was **running perfectly normally** because the strip step never happened. Every
individual component was internally consistent and the overall picture was fiction.

**Instead:** order multi-step state changes so the *recoverable* step comes first, and make the
final flip the last thing that happens. If a step can fail, decide in advance whether the
operation rolls back or is safe to re-run. And when reconciling a split state, trust the
**cluster**, not the bookkeeping.

### Two systems setting the same value produced a manifest that could never apply

A workload template defined a block of environment variables as defaults. The per-instance
configuration also set several of them. The override logic **appended** rather than merged, so
the generated manifest contained duplicate `name` keys in the `env` array.

The result: `duplicate entries for key [name=...]`, GitOps could not apply the Deployment at
all, **no pod was ever created**, and from the outside it simply looked like a deploy that was
taking a very long time to download something. Days were lost watching a graph that was never
going to move.

**Instead:** exactly one layer owns a given value. If templates carry defaults, the merge must
deduplicate — and the failure must be loud. A deploy that produces no pod and no error is worse
than a deploy that fails.

### Running a second deploy engine "in parallel" to test it

A duplicate deploy engine was stood up alongside the working one. It was eventually scaled to
zero and retired without ever taking over. What it left behind was ambiguity — two systems that
could each claim to own a service, shared-namespace drift that showed up as permanent
`OutOfSync` noise, and a period where the answer to "which one deployed this" was "check both".

**Instead:** migrate, don't duplicate. Two systems that both partially own the same thing cost
more than either one does.

### A whole parallel environment, built so the real one could be left alone

**What we did.** Stood up a complete second environment beside the live one, mostly out of
curiosity about whether it was a good idea: its own DNS suffix, its own directory tree of
manifests, its own deploy engine, even its own colour scheme so a screenshot could not be
mistaken for the real thing.

**What happened.** It was abandoned and removed entirely, for the plainest possible reason —
**it was double the trouble.** Every platform change now had two places to land, two things to
verify, and two ways to be half-done. Nothing dramatic failed; the experiment simply cost twice
as much per change as it saved, continuously, and the honest answer was to stop.

**What it cost to unwind**, which is the part worth knowing before you start one: a second deploy
engine and its namespace, the entire parallel manifest tree, and every DNS record for the
parallel suffix, one at a time. One service had to be moved back to a name on the real suffix
afterwards, because at some point it had quietly become the only place that service was
reachable.

**Why this is the appealing idea in this whole file.** A parallel environment sounds like
caution. What it actually buys is **two of everything to maintain, and a testing surface that is
not the thing you run** — and the tell is that last detail: a production service had migrated
into the "experimental" environment without anybody deciding to move it. Once two environments
exist, work flows to whichever is convenient, and the boundary you built stops meaning anything.

**Instead:** isolate with namespaces, not with parallel worlds. If you need somewhere to break
things, deploy a real service you are willing to delete, in the real cluster, on the real domain
— then delete it. That exercises the paths you actually use, which a parallel copy never does.

---

## Git, CI and the registry

### Every build went red ten minutes after succeeding

**What we did.** Upgraded the self-hosted git server by a point release, the way you upgrade
anything.

**What happened.** Builds succeeded, pushed their images, rolled their deployments — and were
then marked **failed** ten to fifteen minutes later. The job logs ended in `Job succeeded`. The
pods were running the new image. The run said failure.

**The signature that identified it, and this is the transferable part:** every failure timestamp
landed on **the same second-offset, on a fixed multiple of five minutes.** Nothing that depends
on how long a build takes fails at a regular interval. A clock does. That pattern alone says
*a periodic sweeper marked this, a build did not* — before you know anything about the cause.

**Why.** The runner reports job completion over an API the server had regressed: the server
rejected a **re-sent** end-of-log message instead of acknowledging it idempotently, returning a
`500`. In the runner, flushing logs gated reporting the final state — so the state report was
never reached, and the runner **logged nothing about it at any level**. The task row stayed
`running` forever, and the server's zombie-task reaper, on its five-minute cycle, eventually
marked it failed. Short jobs escaped, because they never triggered the re-send — which is why it
looked intermittent.

**Instead:**

- **When runs fail on a schedule rather than on a duration, look for a sweeper, not a build.**
- **A runner that goes quiet after "job succeeded" is not evidence that it reported anything.**
  Silence in a client log is compatible with a failed retry loop. Check the *server's* access log
  for the calls it should have received.
- Pin your git server's version and read its changelog before upgrading. This class of break
  lives in the API between server and runner, which is exactly the surface no release note calls
  out and no smoke test covers.
- **Ground truth is the running pod's image digest** — this failure changed nothing about what
  was deployed, only about what the dashboard claimed.

**And the tooling lesson, which cost more than the bug:** our own status tooling omitted
in-progress runs entirely, so during an incident it showed *no* run for a commit that was
actively building, next to three stale red ones. A monitoring surface that hides the current
state while showing old failures is worse than none.

### Pushing large images to a registry on the same machine that hosts it

**What we did.** Built container images on a node and pushed them to the registry running in the
cluster, reached by its public name.

**What happened.** Pushes of large images — ours started failing somewhere in the hundreds of
megabytes; we never established the exact threshold — died with a connection reset. Small images
were fine, which made it look like a flaky network rather than a repeatable limit.

**Why.** The traffic left the machine, went out to the router, and came back to the same machine
— a hairpin — and something in that path did not survive a long single connection. **We never
found out what.** That is the honest state of this entry: we worked around it and moved on.

**Instead**, in the order we would try them now:

1. **Stop building enormous images.** Ours was large because it baked in content that could be
   fetched at runtime instead. Pulling that content at startup removed the problem *and* made
   the image faster to deploy. The workaround was better than the fix would have been.
2. Push to the registry's in-cluster address from a machine inside the cluster, so the traffic
   never leaves it.
3. Only then go looking at the router.

**The general point:** a size-dependent failure between two things on the same machine is
almost always a path problem, not a capacity problem, and the cheapest fix is usually to stop
sending the data rather than to make the path work.

---

## Permissions

### A missing RoleBinding was substituted into every service template as a text placeholder

The deploy tool fetched the internal CA certificate from a secret and substituted it into each
service's template. Its ServiceAccount had no RoleBinding granting access to that secret, so the
API returned `403`. The fetch function returned an **empty string**, and the substitution wrote
its fallback text instead.

The result: **every service deployed by that tool** carried a 28-byte file where its CA
certificate should be, containing the literal words *"CA cert not available"*. Nothing failed at
deploy time. Nothing failed at startup. It failed only when a service tried to make an HTTPS
call to another internal service — a slow-burning fault distributed across nine templates.

**Instead:**

- A function that fetches credentials or certificates must **fail loudly**, not return empty. A
  fallback string that gets written to disk as though it were a certificate is worse than a
  crash, because a crash is discovered immediately.
- Verify RBAC by making the call, not by reading the manifest. And note that a `403` on a
  *cross-namespace* secret read is easy to miss precisely because everything in the tool's own
  namespace works fine.

**A trap in verifying this specific kind of thing:** the API's `permissions` field on a resource
describes what the *account you are asking as* can do, not what some other ServiceAccount can
do. It is not evidence. A `403` from the actual endpoint is evidence.

---

## Documentation

### The notes directory that lost most of itself, and nobody could tell

**What we did.** Kept the working notes for this cluster — build histories, session notes, design
rationale — in an ordinary directory of markdown files. Not in git. It had an index page listing
everything in it. It was cited, by name, as an authoritative source in the standing instructions
we gave our own tooling, **ranked above the version-controlled knowledge base.**

**What happened.** When we finally audited it, the index advertised **78 markdown files. Four
still existed.** The other seventy-odd had been deleted at some point by something, and the index
went on listing them — so every reference was a link to a file that had been gone for months,
looking exactly like a link to a file that was there.

**What it cost, concretely.** A question came up that should have been cheap: *why is the media
stack configured this way?* The document that answered it was one of the deleted ones. There was
no history, no previous revision, no backup — an unversioned file that is deleted is simply gone.
A session was spent reconstructing the reasoning from what survived, and the reconstruction is
strictly worse than the original.

**The second failure, which is subtler and worse.** A small piece of software also lived only in
that directory. When it was finally put into a repository, its code had drifted far past the
design document beside it: it had acquired two permissions and two whole features the document
never mentions, and one file was roughly twenty times the length its plan sketched. **Nobody had
done anything wrong** — there was simply no diff, ever, so documentation and reality separated
silently over months.

**Instead:**

- **If it is worth writing down, it goes in git.** Not for collaboration — for *history*. The
  value is being able to answer "what did this say before" and "when did this change", and an
  unversioned file cannot answer either.
- **An index is not an inventory.** A list of links to files that may or may not exist reports
  the same way whether the files are there or not. If you keep one, generate it from what is
  actually present.
- **Do not cite an unversioned location as a source of truth**, least of all in the instructions
  you hand an agent. It will read what is there, find nothing contradicting it, and be confident.
- **Unversioned code does not merely risk deletion — it stops matching its own documentation**,
  and there is no mechanism by which anyone finds out.

---

## The pattern underneath most of these

Reading them together, one shape recurs far more than any technical cause:

**The system reported success, and the report was true but irrelevant.**

The backup job succeeded — with zero volumes. The pod was `Ready` — and served errors. The
application was `Healthy` — at zero replicas. CI was green — and nothing pulled the image. The
scanner had reports — for the wrong images. The certificate substitution worked — and wrote a
placeholder.

None of these are bugs in the software. Each is a **gap between the thing being measured and the
thing you care about**, and it is the default condition rather than an unusual one. The work of
making a cluster trustworthy is largely the work of closing those gaps deliberately, one at a
time, usually after being burned once.

The corollary, and the reason this file exists: **when a system tells you everything is fine,
the useful question is not "is it lying" but "what exactly is it claiming".**

The mirror image is rarer and just as expensive: **a system reporting failure that is equally
true and equally irrelevant.** Every build going red ten minutes after deploying correctly is
the same gap seen from the other side — the run status was an accurate statement about a
database row, and a worthless statement about whether the software was running. Ask the same
question of a red light that you ask of a green one.
