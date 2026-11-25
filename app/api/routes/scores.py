"""Score parameters and the what-if simulation (for the review with the risk referent)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.api.routes.auth import limiter
from app.core.config import settings
from app.core.security import get_current_user
from app.data.store import DataStore, get_store
from app.services.scoring import HAZARD_NAMES, POINT_SCALES, ScoringParams
from app.services.simulation import simulate

router = APIRouter(prefix="/scores", tags=["scores"], dependencies=[Depends(get_current_user)])
CLASSES = {
    "argiles_points": ("faible", "moyen", "fort", "non cartographié"),
    "seisme_points": ("1", "2", "3", "4", "5"),
    "radon_points": ("1", "2", "3"),
}


class SimulationIn(BaseModel):
    """Anything left out keeps today's value."""

    poids: dict[str, float] | None = None
    seuil_moyen: float | None = Field(default=None, gt=0, lt=100)
    seuil_eleve: float | None = Field(default=None, gt=0, le=100)
    argiles_points: dict[str, float] | None = None
    seisme_points: dict[str, float] | None = None
    radon_points: dict[str, float] | None = None
    tri: Literal["score", "priorite"] = "score"

    @model_validator(mode="after")
    def _check(self) -> "SimulationIn":
        if self.poids is not None:
            if set(self.poids) - set(HAZARD_NAMES) or any(w < 0 or w > 10 for w in self.poids.values()):
                raise ValueError(f"poids: aléas {list(HAZARD_NAMES)}, valeurs entre 0 et 10")
        for scale, classes in CLASSES.items():
            points = getattr(self, scale)
            if points is not None and (set(points) - set(classes) or any(
                p < 0 or p > 100 for p in points.values()
            )):
                raise ValueError(f"{scale}: classes {list(classes)}, valeurs entre 0 et 100")
        return self

    def to_params(self) -> ScoringParams:
        base = ScoringParams.from_settings()
        params = ScoringParams(
            weights={**base.weights, **(self.poids or {})},
            seuil_moyen=self.seuil_moyen if self.seuil_moyen is not None else base.seuil_moyen,
            seuil_eleve=self.seuil_eleve if self.seuil_eleve is not None else base.seuil_eleve,
            **{scale: {**getattr(base, scale), **(getattr(self, scale) or {})} for scale in POINT_SCALES},
        )
        if not params.seuil_moyen < params.seuil_eleve:
            raise ValueError("Le seuil « moyen » doit être inférieur au seuil « élevé »")
        if sum(params.weights.values()) <= 0:
            raise ValueError("Au moins un poids doit être supérieur à 0")
        return params


@router.get("/parametres")
@limiter.limit(settings.rate_limit_default)
def current_parameters(request: Request):
    """Weights, level thresholds and class points (clay, earthquake zone, radon) in use."""
    params = ScoringParams.from_settings()
    return {
        "poids": params.weights,
        "seuil_moyen": params.seuil_moyen,
        "seuil_eleve": params.seuil_eleve,
        **{scale: getattr(params, scale) for scale in POINT_SCALES},
    }


@router.post("/simulation")
@limiter.limit(settings.rate_limit_default)
def run_simulation(request: Request, payload: SimulationIn, store: DataStore = Depends(get_store)):
    """Today's scores, levels and ranks next to the ones these parameters would give."""
    try:
        params = payload.to_params()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return simulate(store, params, payload.tri)
