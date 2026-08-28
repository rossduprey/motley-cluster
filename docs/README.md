# docs — the install path

Read in order. Each layer assumes the one above it is working and verified; see
[`../AGENTS.md`](../AGENTS.md) for the verification gate that closes each phase.

| Section | Covers | Status |
|---|---|---|
| `00-premise/` | what you are building, what it costs, what you need first | **written** — start here |
| `01-nodes/` | OS, k3s install, joining machines, node configuration | **written** — includes `prepare-node.sh` |
| `02-network/` | DNS, ingress, internal certificate authority, TLS | **written** — includes `install-ca-trust.sh` |
| `03-storage/` | replicated block storage on consumer disks, backups | **written** |
| `04-git-ci-registry/` | git, CI runners, and a container registry in-cluster | not yet written |
| `05-gitops/` | continuous reconciliation from a repo; encrypted secrets | not yet written |
| `06-deploying-services/` | generic manifest templates; deploying your own workloads | not yet written |
| `07-observability/` | metrics, dashboards, log aggregation | not yet written |

**"Not yet written" means exactly that.** These sections are being extracted from a cluster
that has been running for months, in the order above. Nothing is stubbed to look finished.
