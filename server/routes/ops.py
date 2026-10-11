"""Ordered operation batches and the accepted operation inventory."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import feed, ops
from ..dependencies import require_device
from ..runtime import _lock, svc

router = APIRouter()


class OpRequest(BaseModel):
    op_id: str = Field(min_length=8, max_length=64)
    op: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)
    device_id: Optional[str] = None

class OpBatch(BaseModel):
    ops: list[OpRequest] = Field(min_length=1, max_length=200)

@router.post("/ops")
def apply_ops(batch: OpBatch, device: str = Depends(require_device)) -> dict:
    """Apply operations in the order given.

    A failure stops the batch: the client's outbox is ordered, and letting later
    operations jump a failed one is how a queue silently corrupts state. The
    response says exactly how far it got.
    """
    results: list[dict] = []
    with _lock:
        service = svc()
        for index, item in enumerate(batch.ops):
            try:
                result = ops.apply_op(
                    service, item.op, item.params, item.op_id, item.device_id or device,
                    request_device=device,
                )
            except ops.OpError as exc:
                return JSONResponse(
                    status_code=exc.status,
                    content={
                        "detail": exc.message,
                        "failed_op_id": item.op_id,
                        "failed_index": index,
                        "applied": results,
                        "seq": feed.current_seq(service.db.connection),
                    },
                )
            results.append({"op_id": item.op_id, "result": result})
        return {"applied": results, "seq": feed.current_seq(service.db.connection)}

@router.get("/ops/known")
def known_ops(device: str = Depends(require_device)) -> dict:
    return {"ops": ops.known_ops()}
