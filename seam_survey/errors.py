"""Error types with stable, machine-readable codes."""


class LedgerError(Exception):
    """Base class for every refusal raised by the ledger."""

    code = "ledger_error"

    def __init__(self, message, *, code=None, details=None):
        super().__init__(message)
        if code is not None:
            self.code = code
        self.details = details or {}


class GeometryError(LedgerError):
    code = "geometry_error"


class RegistrationError(LedgerError):
    code = "registration_error"


class ToleranceError(LedgerError):
    code = "tolerance_error"


class ConflictError(LedgerError):
    code = "conflict_error"


class CoverageError(LedgerError):
    code = "coverage_error"


class SeamError(LedgerError):
    code = "seam_error"
