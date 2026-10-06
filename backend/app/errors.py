class JarvisError(Exception):
    """Base error for a user-facing core-service failure."""


class SetupRequiredError(JarvisError):
    """Raised when the user has not selected a data directory."""


class NotFoundError(JarvisError):
    """Raised when an active entity cannot be found."""


class ValidationError(JarvisError):
    """Raised when a domain-level rule is violated."""


class ProviderError(JarvisError):
    """Raised when a model provider cannot complete an operation."""


class PluginDisabledError(JarvisError):
    """Raised when a request reaches a plugin that is switched off.

    The router stays mounted so the switch takes effect without a restart;
    this error is what the mounted gate answers with.
    """
