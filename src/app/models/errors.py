class AppError(Exception):
    """Base expected application error."""


class NotFoundError(AppError):
    pass


class ConflictError(AppError):
    pass


class DomainValidationError(AppError):
    pass


class ConfigurationError(AppError):
    pass
