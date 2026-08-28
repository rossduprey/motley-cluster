# SPDX-License-Identifier: Apache-2.0
"""A reference deploy engine for the cluster this repository builds.

It reads a catalog entry, renders a template, and commits the result to a git
repository. It has no Kubernetes client and needs no cluster credentials —
see README.md section 2 for why that is the point rather than a limitation.
"""

__all__ = ["catalog", "records", "render", "repo", "validate", "engine", "errors"]

__version__ = "1.0.0"
