"""Documents endpoints: upload, list, stats, detail, file and chunk. (Being implemented.)"""

from fastapi import APIRouter

router = APIRouter(prefix="/documents", tags=["documents"])
