"""Milvus Lite store for the ``candidates`` collection (a-005; §8; IDX-5/IDX-6/IDX-7).

Wraps ``pymilvus.MilvusClient`` against the embedded ``milvus-lite`` engine (a local ``.db``, no
server, no network — AD-083). The collection carries a capability-only dense vector (metric IP),
the exact hard-skill ``skill_set`` ARRAY, a BM25 ``sparse`` vector auto-computed from
``skill_text`` (so a-006 query-time hybrid recall needs no re-index), the scalar filter fields, and
the ``(gold_hash, model_version)`` pair that gates re-embedding (AD-082).

Construction params (``db_path``/``collection``/``dim``/``metric``) are injected by the CLI from
the ``index`` config block — the store reads no config itself (mirrors rank, AD-064). Dates are
stored as INT64 proleptic-Gregorian ordinals (``date.toordinal()``) so a-006 can express numeric
range filters without a re-index; the typed contract keeps ``date | None``.
"""

from __future__ import annotations

import json
from pathlib import Path

from pymilvus import DataType, Function, FunctionType, MilvusClient

from dsm.index.models import CandidateIndexRecord

_CID_MAX = 128
_SKILL_NAME_MAX = 128
_SKILL_SET_CAP = 64
_TEXT_MAX = 8192


class MilvusIndexStore:
    """Idempotent ensure/upsert/delete/version-read over the local ``candidates`` collection."""

    def __init__(
        self,
        db_path: str | Path,
        collection: str = "candidates",
        *,
        dim: int = 768,
        metric: str = "IP",
    ) -> None:
        # Milvus Lite needs the parent dir to exist; the .db file itself is created on open.
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._client = MilvusClient(str(db_path))
        self._collection = collection
        self._dim = dim
        self._metric = metric

    def ensure_collection(self) -> None:
        """Create the ``candidates`` collection + indexes if absent, then load it (idempotent)."""
        if self._client.has_collection(self._collection):
            self._client.load_collection(self._collection)
            return

        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("candidate_id", DataType.VARCHAR, max_length=_CID_MAX, is_primary=True)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=self._dim)
        schema.add_field(
            "skill_set",
            DataType.ARRAY,
            element_type=DataType.VARCHAR,
            max_capacity=_SKILL_SET_CAP,
            max_length=_SKILL_NAME_MAX,
        )
        # BM25 input — analyzer-tokenized; the writer never supplies `sparse` (Function fills it).
        schema.add_field(
            "skill_text", DataType.VARCHAR, max_length=_TEXT_MAX, enable_analyzer=True
        )
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("embed_text", DataType.VARCHAR, max_length=_TEXT_MAX)
        schema.add_field("grade", DataType.VARCHAR, max_length=64)
        schema.add_field("city", DataType.VARCHAR, max_length=_SKILL_NAME_MAX, nullable=True)
        schema.add_field("remote_eligible", DataType.BOOL)
        schema.add_field("availability_type", DataType.VARCHAR, max_length=32)
        schema.add_field("availability_date", DataType.INT64, nullable=True)
        schema.add_field("valid_as_of", DataType.INT64, nullable=True)
        schema.add_field("gold_hash", DataType.VARCHAR, max_length=_CID_MAX)
        schema.add_field("model_version", DataType.VARCHAR, max_length=_CID_MAX)
        schema.add_function(
            Function(
                name="skill_bm25",
                function_type=FunctionType.BM25,
                input_field_names=["skill_text"],
                output_field_names=["sparse"],
            )
        )

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="dense", index_type="AUTOINDEX", metric_type=self._metric
        )
        index_params.add_index(field_name="sparse", index_type="AUTOINDEX", metric_type="BM25")

        self._client.create_collection(self._collection, schema=schema, index_params=index_params)
        self._client.load_collection(self._collection)

    def upsert(self, records: list[CandidateIndexRecord]) -> None:
        """Upsert records by PK (idempotent: identical data → one unchanged entity, IDX-5)."""
        if not records:
            return
        self._client.upsert(self._collection, [self._row(r) for r in records])

    def delete(self, candidate_ids: list[str]) -> None:
        """Delete by ``candidate_id`` — a no-op for ids already absent (tombstones, IDX-7)."""
        if not candidate_ids:
            return
        self._client.delete(self._collection, ids=candidate_ids)

    def fetch_versions(self, candidate_ids: list[str]) -> dict[str, tuple[str, str]]:
        """Return ``{candidate_id: (gold_hash, model_version)}`` for the re-embed gate (IDX-6).

        Missing ids are simply absent from the map (the indexer treats absence as "needs embed").
        """
        if not candidate_ids:
            return {}
        rows = self._client.query(
            self._collection,
            filter=f"candidate_id in {json.dumps(candidate_ids)}",
            output_fields=["candidate_id", "gold_hash", "model_version"],
        )
        return {row["candidate_id"]: (row["gold_hash"], row["model_version"]) for row in rows}

    def _row(self, record: CandidateIndexRecord) -> dict[str, object]:
        """Map a record to a Milvus row — ``sparse`` is omitted (BM25 Function fills it)."""
        return {
            "candidate_id": record.candidate_id,
            "dense": record.dense_vector,
            "skill_set": record.skill_set,
            "skill_text": " ".join(record.skill_set),
            "embed_text": record.embed_text,
            "grade": record.grade.value,
            "city": record.city,
            "remote_eligible": record.remote_eligible,
            "availability_type": record.availability_type,
            "availability_date": (
                record.availability_date.toordinal() if record.availability_date else None
            ),
            "valid_as_of": record.valid_as_of.toordinal() if record.valid_as_of else None,
            "gold_hash": record.gold_hash,
            "model_version": record.model_version,
        }
