# SPDX-License-Identifier: Apache-2.0
"""Every failure mode this engine has a name for.

There is deliberately no "and if that fails, use a default" anywhere in this
package. See README.md section 6: a fetch that fails silently poisons every
service at once.
"""


class EngineError(Exception):
    """Base class. Anything raised here is a deploy that must not proceed."""


class CatalogError(EngineError):
    """The catalog is malformed, or the entry asked for is not in it."""


class TemplateError(EngineError):
    """The template is missing, unparseable, or leaves tokens unresolved."""


class InputError(EngineError):
    """A required input — a certificate, a credential — could not be read.

    This exists to be raised. It is the whole point of the module.
    """


class ValidationError(EngineError):
    """The rendered manifest is wrong, caught before anything was committed."""


class RepoError(EngineError):
    """A git operation failed."""
