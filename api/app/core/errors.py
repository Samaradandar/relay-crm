# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Standard error responses: {code, message, details}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "error"

    def __init__(self, message: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details or {}


class PermissionDenied(AppError):
    status_code = 403
    code = "permission_denied"


class OutOfScope(AppError):
    """Write outside scope: 403. For reads raise NotFound so existence does not leak."""

    status_code = 403
    code = "out_of_scope"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class ReasonRequired(AppError):
    status_code = 422
    code = "reason_required"


class ValidationFailed(AppError):
    """A value is not acceptable (bad phone, past due date...). Shown next to the field."""

    status_code = 422
    code = "validation_error"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class CriteriaIncomplete(AppError):
    """A forward move was asked while exit criteria are not all ticked."""

    status_code = 422
    code = "criteria_incomplete"


class ProofRequired(AppError):
    """A criterion cannot be ticked because its proof does not exist yet."""

    status_code = 422
    code = "proof_required"


class DuplicateBlocked(Conflict):
    """An exact duplicate (domain, phone or email) exists; creation is blocked."""

    code = "duplicate_blocked"


class DuplicateWarning(Conflict):
    """A similar company exists; the caller may continue with 'Create anyway' and a reason."""

    code = "duplicate_warning"


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"


class StorageUnavailable(AppError):
    """File storage (S3/MinIO) is not configured or not reachable, e.g. the no-Docker local mode."""

    status_code = 503
    code = "storage_unavailable"


class ApprovalRequested(AppError):
    """Not a failure: the action was turned into an approval request (202)."""

    status_code = 202
    code = "approval_requested"


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details), status_code=exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(
            error_body("validation_error", "Invalid request", {"errors": errors}),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(error_body("http_error", exc.detail), status_code=exc.status_code)

    @app.exception_handler(DBAPIError)
    async def _db_error(_: Request, exc: DBAPIError) -> JSONResponse:
        # A value PostgreSQL cannot store (NUL byte, number beyond 64 bits) is the caller's fault.
        kind = type(exc.orig).__name__ if exc.orig is not None else ""
        cause = getattr(exc.orig, "__cause__", None)
        names = {kind, type(cause).__name__} if cause is not None else {kind}
        if names & {"DataError", "CharacterNotInRepertoire", "NumericValueOutOfRangeError"} or any(
            "DataError" in n or "Repertoire" in n or "OutOfRange" in n for n in names
        ):
            return JSONResponse(
                error_body("validation_error", "A value is not acceptable", {}), status_code=422
            )
        return _server_error_response(exc)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        return _server_error_response(exc)


def _server_error_response(exc: Exception) -> JSONResponse:
    log.exception("unhandled error", exc_info=exc)
    return JSONResponse(error_body("internal_error", "Something went wrong"), status_code=500)
