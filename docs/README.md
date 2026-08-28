# docs — the install path

Read in order. Each layer assumes the one above it is working and verified; see
[`../AGENTS.md`](../AGENTS.md) for the verification gate that closes each phase.

| Section | Covers | Status |
|---|---|---|
| `00-premise/` | what you are building, what it costs, what you need first | **written** — start here |
| `01-nodes/` | OS, k3s install, joining machines, node configuration | **written** — includes `prepare-node.sh` |
| `02-network/` | DNS, ingress, internal certificate authority, TLS | **written** — includes `install-ca-trust.sh` |
| `03-storage/` | replicated block storage on consumer disks, backups | **written** |
| `04-git-ci-registry/` | git, CI runners, and a container registry in-cluster | **written** — includes `registries.yaml.example` |
| `05-gitops/` | continuous reconciliation from a repo; encrypted secrets | **written** |
| `06-deploying-services/` | generic manifest templates; deploying your own workloads | **written** — includes `service-template.yaml.example` |
| `07-observability/` | metrics, logs, alerts, and probing from outside | **written** — the last platform layer |
| `08-the-deploy-engine/` | the capstone: making deployment a matter of naming a service | not yet written |

**"Not yet written" means exactly that.** These sections are being extracted from a cluster
that has been running for months, in the order above. Nothing is stubbed to look finished.

**`00`–`07` are the platform.** They are complete on their own: at the end of `07` you have a
cluster that runs services, survives a node dying, and tells you when it does not. **`08` is the
payoff** — the component that turns every constraint the earlier sections asked you to adopt into
a single action, and the reason those constraints are worth obeying. It ships as working,
freely-licensed source, not as a description.
