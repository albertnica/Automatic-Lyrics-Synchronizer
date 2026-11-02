"""
server/routers/models.py
Endpoints for Hugging Face Hub model discovery, search autocomplete, and local cache inspection.

Why:
Empowers users to dynamically search and configure ASR models from the Web Studio UI.
"""

from typing import List
from fastapi import APIRouter, Query

from modules import model_manager
from server.schemas import ModelInfo, ModelCatalogResponse

router = APIRouter(prefix="/api/models", tags=["Models"])


@router.get("/catalog", response_model=ModelCatalogResponse)
async def get_model_catalog() -> ModelCatalogResponse:
    """
    Fetch curated ASR models, currently cached models, and local cache directory path.
    """
    catalog = model_manager.get_model_status_catalog()
    return ModelCatalogResponse(
        recommended=[ModelInfo(**m) for m in catalog["recommended"]],
        cached_models=catalog["cached_models"],
        cache_dir=catalog["cache_dir"],
    )


@router.get("/search", response_model=List[ModelInfo])
async def search_models(
    q: str = Query("", description="Search query e.g. 'openai' or 'whisper'"),
    limit: int = Query(12, ge=1, le=50, description="Max results returned"),
) -> List[ModelInfo]:
    """
    Live autocomplete search querying the Hugging Face Hub API for ASR models.
    """
    results = model_manager.search_hub_models(query=q, limit=limit)
    return [ModelInfo(**m) for m in results]

