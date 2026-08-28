# findings — what we learned, including what failed

The part that is hard to get anywhere else. Documentation of a working system tells you the
path that succeeded; this directory tells you the ones that did not, which is usually the more
expensive knowledge to reacquire.

| File | Covers | Status |
|---|---|---|
| `what-worked.md` | choices that held up under months of running, each with its cost | **written** |
| `what-did-not.md` | failures and dead ends, with the reasoning and what to do instead | **written** — read it before you debug anything |
| `incidents/` | individual failures, with the evidence captured at the time | not yet written |
| `measurements.md` | real numbers, each with the method used to get it | not yet written |

**Ground rules for everything in here:**

- **Evidence at the time it was found.** The log line, the command output, the error. That is
  the expensive part to reconstruct later; the narrative is not.
- **How a number was measured is part of the number.** A figure without its method is an
  opinion with a decimal point.
- **A failed approach is written up with the same care as a successful one.** Knowing which
  road is closed, and why, is worth as much as knowing which one is open.
