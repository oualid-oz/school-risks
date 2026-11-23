"""Take and read yearly score snapshots."""
from __future__ import annotations

import hashlib
import json

from sqlmodel import Session, delete, select

from app.core.config import settings
from app.data.store import DataStore
from app.models.snapshots import ScoreSnapshot, ScoreSnapshotRead
from app.services.scoring import HAZARDS


class SnapshotExists(RuntimeError):
    pass


def _file_sha256() -> str:
    digest = hashlib.sha256()
    with open(settings.data_dir / settings.risques_file, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def take_snapshot(
    session: Session, store: DataStore, annee: int, replace: bool = False
) -> int:
    """Store the current score of every school for ``annee``. Returns the row count."""
    existing = session.exec(select(ScoreSnapshot.id).where(ScoreSnapshot.annee == annee)).first()
    if existing is not None:
        if not replace:
            raise SnapshotExists(f"A snapshot for {annee} already exists (use --replace)")
        session.exec(delete(ScoreSnapshot).where(ScoreSnapshot.annee == annee))

    poids = json.dumps({h: getattr(settings, w) for h, (_, w) in HAZARDS.items()}, sort_keys=True)
    sha = _file_sha256()
    count = 0
    for uai, p in sorted(store.all_profiles().items()):
        college = store.get_college(uai)
        session.add(
            ScoreSnapshot(
                annee=annee,
                uai=uai,
                score_global=p["score_global"],
                niveau=p["niveau"],
                alea_principal=p["alea_principal"],
                effectif_total=college["effectif_total"],
                indice_priorite=p["indice_priorite"],
                aleas=json.dumps(p["aleas"], sort_keys=True),
                poids=poids,
                source_sha256=sha,
            )
        )
        count += 1
    session.commit()
    return count


def history_for(session: Session, uai: str) -> list[ScoreSnapshotRead]:
    rows = session.exec(
        select(ScoreSnapshot).where(ScoreSnapshot.uai == uai).order_by(ScoreSnapshot.annee)
    ).all()
    return [
        ScoreSnapshotRead(
            annee=r.annee,
            score_global=r.score_global,
            niveau=r.niveau,
            alea_principal=r.alea_principal,
            effectif_total=r.effectif_total,
            indice_priorite=r.indice_priorite,
            aleas=json.loads(r.aleas),
            created_at=r.created_at,
        )
        for r in rows
    ]
