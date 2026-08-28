# reference-engine

A working deploy engine, licensed Apache-2.0: 1,407 lines of Python across nine modules, of
which 895 are code and the rest is comment and docstring — much of it explaining what a
particular line is defending against.

It reads a catalog entry, renders a template, validates the result, and commits it to your
GitOps repository. **It has no Kubernetes client and needs no cluster credentials.** That is
the design, not a limitation — [`../README.md`](../README.md) §2 and §6 are the argument, and
one of the tests asserts it mechanically.

This is a **reference**: complete enough to run, small enough to read in a sitting, and meant
to be taken and changed. If you read it and decide to write your own, that is a good outcome.

---

## Run it

```bash
pip install pyyaml                    # the only dependency
cp engine.yaml.example engine.yaml    # fill in the placeholders
python -m deploy_engine.cli --config engine.yaml list
python -m deploy_engine.cli --config engine.yaml deploy notepad
python -m deploy_engine.cli --config engine.yaml status
```

Commands: `deploy`, `suspend`, `resume`, `remove`, `status`, `reconcile`, `list`.

It is a command rather than a web service on purpose. A web service is the right eventual
shape and also the shape that invites a background thread, a job id, and a status field that
lies after a restart. Start with something that either finishes or fails.

## Run the tests

```bash
python -m unittest discover -s tests -t tests -v
```

53 tests, no network, no cluster, no test framework to install — the fixtures build two real
git repositories in a temporary directory and drive the engine against them. CI runs the same
command on every push.

## What is where

| File | What it holds |
|---|---|
| `deploy_engine/catalog.py` | Loading and validating the catalog. An unknown field is an error, not a shrug. |
| `deploy_engine/render.py` | Template + entry → objects. Substitution happens **after** YAML parsing. |
| `deploy_engine/validate.py` | Every check that runs before anything is committed. |
| `deploy_engine/repo.py` | Git, and the certificate read that raises rather than returning a default. |
| `deploy_engine/records.py` | Lifecycle records, and reconciliation that derives state from the manifests. |
| `deploy_engine/engine.py` | The orchestration, and the commit ordering. |
| `deploy_engine/cli.py` | The command line. |
| `templates/stateful.yaml` | One worked template. Also the baked-in offline fallback. |
| `engine.yaml.example` | Settings. Note what it has no field for. |

## The parts that exist because of a specific failure

Each of these is a scar from [`../README.md`](../README.md) §6, and each has a test named after
what it prevents.

**Substitution happens after parsing, never on raw text.** The template is parsed as YAML and
tokens are replaced in the resulting *values*. Comments are gone before substitution happens,
so the failure where a multi-line certificate was substituted into a comment and broke out of
it is not a bug that was fixed — it is a bug this design cannot express. As a bonus, a lone
token keeps its type: `containerPort: APP_PORT` yields an integer, not the string `"8080"`
that a text-substituting engine produces and the API server rejects.

**`read_ca_certificate()` raises. It has no fallback.** The version with one wrote the literal
words *"CA cert not available"* into every service the engine ever deployed, and nothing failed
for weeks. The certificate is read from the source repository, so the engine needs no cluster
access to get it.

**Overrides replace, never append** — except `initContainers`, where ordering makes appending
correct. `_replace_by_name()` merges by name and cannot produce a duplicate; `validate.py`
then checks for duplicates anyway, because "the merge is careful" is exactly what we believed
last time.

**Validation runs before the first write and reports every problem at once.** One problem per
run turns a bad template into an afternoon of fix-rerun-fix-rerun.

**Manifests are committed before the lifecycle record, always.** Stopping in the middle leaves
manifests with no record, which reconciliation adopts. The reverse leaves a record claiming a
service that was never committed.

**Reconciliation derives state from the manifests**, not from a status field. An interrupted
suspend once left a record saying *suspended* while the cluster happily served the service, and
neither suspend nor resume would touch it again because each expected the other's state.
`records.reconcile()` reads what is actually committed and corrects the record; every lifecycle
command runs it first, so re-running a half-finished operation **finishes** it.

**Nothing here reports on the cluster.** `status` returns what the engine committed and says so
in the payload. Committing is not deploying: until your controller reconciles — and until the
root app-of-apps creates this service's application object — nothing has happened in the
cluster yet. That is normal timing, and the engine refuses to pretend otherwise.

## What it deliberately does not do

Deploy into an existing namespace. Deploy third-party Helm charts. Own hand-authored services.
Talk to a cluster. [`../README.md`](../README.md) §7 has the reasoning for the first three, and
§2 for the last.

## If you take it

You will want to change: the `Application` object in `engine.py` (shaped for Argo CD), the
template set, the override fields, and probably the record schema. The parts worth keeping are
the ordering, the raising, and the validation — those are the parts that were paid for.
