class DomainError(Exception):
    """A domain rule was violated."""


class InvalidValue(DomainError):
    pass


class ScheduleMismatch(DomainError):
    """The snapshot belongs to a different locality than the schedule."""
