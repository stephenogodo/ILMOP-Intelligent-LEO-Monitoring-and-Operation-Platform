"""
services/api/routers/commands.py

REST endpoints for the command path (Sprint 9, patch C).

    POST /auth/login                  name + secret  -> bearer token
    POST /auth/logout                 end the session
    POST /commands                    submit a DRAFT            (AUTHOR)
    GET  /commands/{id}               read a command            (any session)
    POST /commands/{id}/approve       DRAFT -> APPROVED         (APPROVER, human)
    POST /commands/{id}/reject        DRAFT -> REJECTED         (APPROVER, human)
    POST /commands/{id}/uplink        publish to the satellite  (UPLINKER)

THIN BY DESIGN
──────────────
These functions only translate HTTP to a SecureCommandService call and the
result (or error) back to HTTP.  Authentication, roles, the at-most-once
uplink claim and the audit log all live in the services below, so the API
cannot loosen a rule.

The routes are plain `def`, not `async def`: FastAPI then runs them in its
thread pool, so a slow password hash or a Kafka flush never blocks the event
loop.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from services.api.command_runtime import CommandRuntime
from services.command.auth import AuthenticationError, AuthorizationError
from services.command.authoring import CommandValidationError
from services.command.lifecycle import ApprovalError, InvalidTransitionError
from services.command.service import CommandNotFoundError, IdempotencyConflictError

router = APIRouter(tags=["commands"])


# ── Request bodies ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    name:   str
    secret: str


class SubmitRequest(BaseModel):
    satellite_id:    str
    command_type:    str
    parameters:      Dict[str, Any] = Field(default_factory=dict)
    # Send the same key on a retry and you get the original command back,
    # not a second one.
    idempotency_key: Optional[str] = None


class RejectRequest(BaseModel):
    reason: str


# ── Dependencies ─────────────────────────────────────────────────────────────

def get_runtime(request: Request) -> CommandRuntime:
    """
    The shared command machinery, or 503 if no accounts were configured.

    Tests replace this with app.dependency_overrides[get_runtime].
    """
    runtime = getattr(request.app.state, "commands", None)
    if runtime is None:
        raise HTTPException(503, "command endpoints are not available: no accounts configured")
    return runtime


def get_token(authorization: Optional[str] = Header(default=None)) -> str:
    """Extract the token from 'Authorization: Bearer <token>'; 401 if absent or malformed."""
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})


# ── Error translation ────────────────────────────────────────────────────────

def _translate(exc: Exception) -> HTTPException:
    """Map a service error to the HTTP error a client should see."""
    if isinstance(exc, AuthenticationError):
        return HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"})
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, CommandNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, CommandValidationError):
        return HTTPException(422, str(exc))
    if isinstance(exc, (IdempotencyConflictError, InvalidTransitionError, ApprovalError)):
        return HTTPException(409, str(exc))
    raise exc          # anything else is a genuine bug: let it surface as a 500


def _body(command) -> dict:
    """A command as plain JSON-safe data (enums and datetimes become text)."""
    return command.model_dump(mode="json")


# ── Login / logout ───────────────────────────────────────────────────────────

@router.post("/auth/login")
def login(body: LoginRequest, rt: CommandRuntime = Depends(get_runtime)):
    """Check credentials and return a session token (401 for any failure, locked or not)."""
    try:
        token = rt.auth.login(body.name, body.secret)
    except AuthenticationError as exc:
        raise _translate(exc)
    return {"token": token, "token_type": "bearer"}


@router.post("/auth/logout", status_code=204)
def logout(token: str = Depends(get_token), rt: CommandRuntime = Depends(get_runtime)):
    """End the session.  Unknown tokens are ignored, so this never fails."""
    rt.auth.logout(token)
    return Response(status_code=204)


# ── Commands ─────────────────────────────────────────────────────────────────

@router.post("/commands")
def submit(body: SubmitRequest, response: Response,
           token: str = Depends(get_token), rt: CommandRuntime = Depends(get_runtime)):
    """Author a DRAFT.  201 when new, 200 when an earlier identical request is returned."""
    try:
        result = rt.secure.submit(token, body.satellite_id, body.command_type,
                                  body.parameters, body.idempotency_key)
    except Exception as exc:
        raise _translate(exc)
    response.status_code = 201 if result.created else 200
    return _body(result.command)


@router.get("/commands/{command_id}")
def read(command_id: str,
         token: str = Depends(get_token), rt: CommandRuntime = Depends(get_runtime)):
    """Read one command with its full history."""
    try:
        command = rt.secure.get(token, command_id)
    except Exception as exc:
        raise _translate(exc)
    if command is None:
        raise HTTPException(404, f"command {command_id} not found")
    return _body(command)


@router.post("/commands/{command_id}/approve")
def approve(command_id: str,
            token: str = Depends(get_token), rt: CommandRuntime = Depends(get_runtime)):
    """DRAFT -> APPROVED.  Needs APPROVER and a human OPERATOR account."""
    try:
        return _body(rt.secure.approve(token, command_id))
    except Exception as exc:
        raise _translate(exc)


@router.post("/commands/{command_id}/reject")
def reject(command_id: str, body: RejectRequest,
           token: str = Depends(get_token), rt: CommandRuntime = Depends(get_runtime)):
    """DRAFT -> REJECTED with a reason.  Needs APPROVER and a human OPERATOR account."""
    try:
        return _body(rt.secure.reject(token, command_id, body.reason))
    except Exception as exc:
        raise _translate(exc)


@router.post("/commands/{command_id}/uplink")
def uplink(command_id: str,
           token: str = Depends(get_token), rt: CommandRuntime = Depends(get_runtime)):
    """
    Claim the command and publish it for the satellite.  Needs UPLINKER.

    Returns at once; the command stays UPLINKED until the satellite's
    acknowledgement arrives (poll GET /commands/{id}).  The body says whether
    this call sent it ("sent") and, if not, why ("reason").
    """
    if rt.sender is None:
        raise HTTPException(503, "uplink is not enabled (set COMMANDS_ENABLED=true)")
    try:
        result = rt.secure.uplink_async(token, command_id, rt.sender)
    except Exception as exc:
        raise _translate(exc)
    return {"sent": result.sent, "reason": result.reason, "command": _body(result.command)}
