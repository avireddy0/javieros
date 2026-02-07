import importlib
import logging

from fastapi import APIRouter


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/ide")
async def ide_hook():
    try:
        import antigravity

        importlib.reload(antigravity)
    except Exception as err:
        logger.warning("antigravity hook failed: %s", err)
    return {"status": "ok"}
