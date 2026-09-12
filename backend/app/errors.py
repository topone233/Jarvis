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
