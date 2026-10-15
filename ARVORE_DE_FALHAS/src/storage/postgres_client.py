"""
Cliente PostgreSQL — tabelas, CRUD e queries para o pipeline de arvore de falhas.

Todas as tabelas possuem org_id + tenant_id para multi-tenancy.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import MetaData, Table, Column, String, Text, Integer, SmallInteger, Float, Boolean, DateTime, BigInteger, Index, UniqueConstraint, create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool

from src.config import settings


class PostgresClient:
    """Cliente PostgreSQL com ORM leve (tables declaradas via MetaData)."""

    def __init__(self) -> None:
        self.engine = create_engine(
            settings.POSTGRES_URI,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
        )
        self.SessionFactory = sessionmaker(
            bind=self.engine, autocommit=False, autoflush=False
        )
        self.metadata = MetaData()
        self._define_schema()

    # ── Schema ────────────────────────────────────────────────────

    def _define_schema(self) -> None:
        t = self.metadata.tables
        if "bronze_documents" in t:
            return

        Table(
            "bronze_documents", self.metadata,
            Column("id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("file_name", String, nullable=False),
            Column("file_type", String, nullable=False),
            Column("file_size", BigInteger),
            Column("document_hash", String, nullable=False),
            Column("object_path", String),
            Column("metadata", Text),
            Column("status", String, default="ingested"),
            Column("upload_date", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            UniqueConstraint("org_id", "tenant_id", "document_hash", name="uq_bronze_org_tenant_hash"),
        )

        Table(
            "silver_documents", self.metadata,
            Column("id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("bronze_id", String, nullable=False),
            Column("extracted_text", Text),
            Column("summary", Text),
            Column("annotations", Text),
            Column("entities", Text),
            Column("processing_date", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            Column("object_path", String),
            Column("bedrock_model", String),
            Column("extraction_method", String),
            Column("status", String, default="processed"),
        )

        Table(
            "gold_analytics", self.metadata,
            Column("id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("silver_id", String),
            Column("document_type", String),
            Column("summary", Text),
            Column("key_insights", Text),
            Column("classification", Text),
            Column("analysis_date", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            Column("object_path", String),
            Column("status", String, default="completed"),
        )

        Table(
            "envelope_documents", self.metadata,
            Column("id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("bronze_id", String, nullable=False),
            Column("document_type", String, nullable=False),
            Column("document_hash", String, nullable=False),
            Column("file_name", String, nullable=False),
            Column("is_structured", Boolean, default=False),
            Column("confidence_score", Float),
            Column("extracted_data", Text),
            Column("raw_text", Text),
            Column("processed_at", DateTime(timezone=True)),
            Column("status", String, default="processed"),
        )

        Table(
            "document_images", self.metadata,
            Column("id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("bronze_id", String, nullable=False),
            Column("page_number", Integer),
            Column("image_index", Integer),
            Column("image_hash", String),
            Column("object_path", String),
            Column("width", Integer),
            Column("height", Integer),
            Column("format", String),
            Column("file_size", BigInteger),
            Column("x_position", Float),
            Column("y_position", Float),
            Column("colorspace", String),
            Column("page_context", Text),
            Column("extracted_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            Column("vlm_description", Text),
            Column("vlm_category", String),
            Column("vlm_tags", Text),
            Column("vlm_detected_objects", Text),
            Column("vlm_technical_context", Text),
            Column("vlm_text_content", Text),
            Column("vlm_quality_indicators", Text),
            Column("vlm_confidence", Float),
            Column("status", String, default="cataloged"),
        )

        Table(
            "image_links", self.metadata,
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("source_image_id", String, nullable=False),
            Column("target_image_id", String),
            Column("link_type", String),
            Column("score", Float),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            UniqueConstraint("org_id", "tenant_id", "source_image_id", "target_image_id", "link_type", name="uq_image_link"),
        )

        Table(
            "image_cluster_runs", self.metadata,
            Column("run_id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("num_images", Integer),
            Column("method", String),
            Column("linkage_json", Text),
            Column("params", Text),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
        )

        Table(
            "image_clusters", self.metadata,
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("run_id", String, nullable=False),
            Column("image_id", String, nullable=False),
            Column("cluster_label", Integer),
            UniqueConstraint("run_id", "image_id", name="uq_image_cluster_run_image"),
        )

        Table(
            "processing_logs", self.metadata,
            Column("id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("document_id", String),
            Column("layer", String),
            Column("operation", String),
            Column("status", String),
            Column("error_message", Text),
            Column("metadata", Text),
            Column("timestamp", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
        )

        Table(
            "event_outbox", self.metadata,
            Column("id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("event_type", String, nullable=False),
            Column("routing_key", String),
            Column("payload", Text),
            Column("status", String, default="pending"),
            Column("attempts", Integer, default=0),
            Column("last_error", Text),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            Column("published_at", DateTime(timezone=True)),
            Column("next_attempt_at", DateTime(timezone=True)),
        )

        Table(
            "ingest_jobs", self.metadata,
            Column("id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("file_name", String),
            Column("status", String, default="pending"),
            Column("bronze_id", String),
            Column("envelope_id", String),
            Column("document_type", String),
            Column("result", Text),
            Column("error", Text),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            Column("updated_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
        )

        fault_trees = Table(
            "fault_trees", self.metadata,
            Column("id", String, primary_key=True),
            Column("org_id", String, nullable=False),
            Column("model_id", String),
            Column("tenant_id", String),
            Column("equipment_id", String),
            Column("tree_data", Text, nullable=False),
            Column("failure_modes", Text),
            Column("generation_status", String),
            Column("generation_error", Text),
            Column("generation_updated_at", DateTime(timezone=True)),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            Column("updated_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
        )
        Index(
            "uq_fault_tree_model_scope",
            fault_trees.c.org_id,
            fault_trees.c.model_id,
            unique=True,
            postgresql_where=(
                fault_trees.c.equipment_id.is_(None)
                & fault_trees.c.model_id.is_not(None)
            ),
        )
        Index(
            "uq_fault_tree_equipment_scope",
            fault_trees.c.org_id,
            fault_trees.c.tenant_id,
            fault_trees.c.equipment_id,
            unique=True,
            postgresql_where=fault_trees.c.equipment_id.is_not(None),
        )

        Table(
            "document_bindings", self.metadata,
            Column("id", String, primary_key=True),
            Column("document_id", String, nullable=False),
            Column("envelope_id", String),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("target_type", String, nullable=False),
            Column("target_id", String, nullable=False),
            Column("model_id", String),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            UniqueConstraint(
                "document_id", "target_type", "target_id", "org_id", "tenant_id",
                name="uq_document_binding_tenant_scoped",
                postgresql_nulls_not_distinct=True,
            ),
        )

        Table(
            "fault_tree_versions", self.metadata,
            Column("id", String, primary_key=True),
            Column("tree_id", String, nullable=False),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String),
            Column("model_id", String),
            Column("equipment_id", String),
            Column("document_id", String),
            Column("envelope_id", String),
            Column("file_name", String),
            Column("status", String, nullable=False, default="queued"),
            Column("failure_modes", Text),
            Column("error", Text),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            Column("updated_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            Column("activated_at", DateTime(timezone=True)),
            Column("supersedes_version_id", String, nullable=True),
            Column("progress", SmallInteger, nullable=True),
            Column("progress_phase", String, nullable=True),
            Column("progress_message", Text, nullable=True),
        )

        Table(
            "fault_tree_version_documents", self.metadata,
            Column("id", String, primary_key=True),
            Column("version_id", String, nullable=False),
            Column("document_id", String, nullable=False),
            Column("envelope_id", String),
            Column("relationship", String, nullable=False, default="source"),
            Column("origin_type", String, nullable=False),
            Column("origin_id", String, nullable=False),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            UniqueConstraint(
                "version_id", "document_id", "relationship",
                name="uq_fault_tree_version_document",
            ),
        )

        Table(
            "chat_history", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("equipment_id", String, nullable=False),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String, nullable=False),
            Column("seq", Integer, nullable=False),
            Column("role", String, nullable=False),
            Column("content", Text, nullable=False),
            Column("ts", Integer, nullable=False),
        )

        Table(
            "chat_summaries", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("equipment_id", String, nullable=False),
            Column("org_id", String, nullable=False),
            Column("tenant_id", String, nullable=False),
            Column("summary", Text),
            Column("summarized_upto", Integer, default=0),
            Column("updated_at", DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
            UniqueConstraint("org_id", "equipment_id", "tenant_id", name="uq_chat_summary_org_equip_tenant"),
        )

    def create_tables(self) -> None:
        try:
            self._define_schema()
            self.metadata.create_all(self.engine)
            logger.info("Tabelas PostgreSQL criadas/verificadas")
        except Exception as e:
            logger.error(f"Erro ao criar tabelas: {e}")
            raise

    def get_session(self) -> Session:
        return self.SessionFactory()

    # ── Bronze ────────────────────────────────────────────────────

    def insert_bronze_document(self, data: Dict[str, Any]) -> bool:
        if not data.get("org_id"):
            return False
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "INSERT INTO bronze_documents "
                        "(id, org_id, tenant_id, file_name, file_type, file_size, "
                        "document_hash, object_path, metadata, status, upload_date) "
                        "VALUES (:id, :org_id, :tenant_id, :file_name, :file_type, "
                        ":file_size, :document_hash, :object_path, :metadata, :status, :upload_date)"
                    ),
                    {
                        "id": data["id"],
                        "org_id": data["org_id"],
                        "tenant_id": data["tenant_id"],
                        "file_name": data["file_name"],
                        "file_type": data["file_type"],
                        "file_size": data.get("file_size"),
                        "document_hash": data["document_hash"],
                        "object_path": data.get("object_path"),
                        "metadata": data.get("metadata"),
                        "status": data.get("status", "ingested"),
                        "upload_date": datetime.now(timezone.utc),
                    },
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao inserir bronze: {e}")
            return False

    def get_bronze_document(
        self,
        bronze_id: str,
        org_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retorna bronze document por id; tenant_id=None aceita documentos org-scoped."""
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT id, org_id, tenant_id, file_name, file_type, "
                        "object_path, metadata, document_hash, status, upload_date "
                        "FROM bronze_documents "
                        "WHERE id = :id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id LIMIT 1"
                    ),
                    {"id": bronze_id, "org_id": org_id, "tenant_id": tenant_id},
                ).fetchone()
                if row is not None:
                    return dict(row._mapping)
        except Exception as e:
            logger.error(f"Erro ao buscar bronze por id: {e}")
        return None

    def get_bronze_document_any_tenant(
        self,
        bronze_id: str,
        org_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Retorna bronze document por id sem filtrar por tenant (uso interno do resolver)."""
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT id, org_id, tenant_id, file_name, file_type, "
                        "object_path, metadata, document_hash, status, upload_date "
                        "FROM bronze_documents "
                        "WHERE id = :id AND org_id = :org_id LIMIT 1"
                    ),
                    {"id": bronze_id, "org_id": org_id},
                ).fetchone()
                if row is not None:
                    return dict(row._mapping)
        except Exception as e:
            logger.error(f"Erro ao buscar bronze por id (any tenant): {e}")
        return None

    def get_envelope_by_bronze(
        self,
        bronze_id: str,
        org_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retorna o envelope mais recente associado a um bronze_id."""
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT id, org_id, tenant_id, bronze_id, document_type, "
                        "file_name, status, processed_at "
                        "FROM envelope_documents "
                        "WHERE bronze_id = :bronze_id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id "
                        "ORDER BY processed_at DESC LIMIT 1"
                    ),
                    {"bronze_id": bronze_id, "org_id": org_id, "tenant_id": tenant_id},
                ).fetchone()
                if row is not None:
                    return dict(row._mapping)
        except Exception as e:
            logger.error(f"Erro ao buscar envelope por bronze: {e}")
        return None

    def get_envelope_by_bronze_any_tenant(
        self,
        bronze_id: str,
        org_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Retorna envelope por bronze_id sem filtrar tenant (uso interno do resolver)."""
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT id, org_id, tenant_id, bronze_id, document_type, "
                        "file_name, status, processed_at "
                        "FROM envelope_documents "
                        "WHERE bronze_id = :bronze_id AND org_id = :org_id "
                        "ORDER BY processed_at DESC LIMIT 1"
                    ),
                    {"bronze_id": bronze_id, "org_id": org_id},
                ).fetchone()
                if row is not None:
                    return dict(row._mapping)
        except Exception as e:
            logger.error(f"Erro ao buscar envelope por bronze (any tenant): {e}")
        return None

    def find_bronze_by_hash(
        self, org_id: str, tenant_id: Optional[str], document_hash: str
    ) -> Optional[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT id, file_name, upload_date, status, object_path, metadata "
                        "FROM bronze_documents "
                        "WHERE org_id = :org_id AND tenant_id IS NOT DISTINCT FROM :tenant_id "
                        "AND document_hash = :hash LIMIT 1"
                    ),
                    {"org_id": org_id, "tenant_id": tenant_id, "hash": document_hash},
                ).fetchone()
                if row is not None:
                    return dict(row._mapping)
        except Exception as e:
            logger.error(f"Erro ao buscar bronze por hash: {e}")
        return None

    def merge_bronze_metadata(
        self, org_id: str, tenant_id: Optional[str], bronze_id: str, metadata: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT metadata FROM bronze_documents "
                        "WHERE id = :id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                    ),
                    {"id": bronze_id, "org_id": org_id, "tenant_id": tenant_id},
                ).fetchone()
                if row is None:
                    return None
                existing_raw = row[0]
                existing = json.loads(existing_raw) if existing_raw else {}
                existing_contexts = set(existing.get("context_ids", []))
                new_contexts = set(metadata.get("context_ids", []))
                merged_contexts = list(existing_contexts | new_contexts)
                existing.update(metadata)
                existing["context_ids"] = merged_contexts
                session.execute(
                    text(
                        "UPDATE bronze_documents SET metadata = :metadata "
                        "WHERE id = :id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                    ),
                    {
                        "metadata": json.dumps(existing, ensure_ascii=False),
                        "id": bronze_id,
                        "org_id": org_id,
                        "tenant_id": tenant_id,
                    },
                )
                session.commit()
                return existing
        except Exception as e:
            logger.error(f"Erro ao merge metadata: {e}")
            return None

    # ── Envelope ──────────────────────────────────────────────────

    def insert_envelope(self, data: Dict[str, Any]) -> bool:
        if not data.get("org_id"):
            return False
        try:
            with self.get_session() as session:
                extracted = data.get("extracted_data")
                extracted_str = json.dumps(extracted, ensure_ascii=False) if extracted else None
                session.execute(
                    text(
                        "INSERT INTO envelope_documents "
                        "(id, org_id, tenant_id, bronze_id, document_type, document_hash, "
                        "file_name, is_structured, confidence_score, extracted_data, "
                        "raw_text, processed_at, status) "
                        "VALUES (:id, :org_id, :tenant_id, :bronze_id, :document_type, "
                        ":document_hash, :file_name, :is_structured, :confidence_score, "
                        ":extracted_data, :raw_text, :processed_at, :status)"
                    ),
                    {
                        "id": data["id"],
                        "org_id": data["org_id"],
                        "tenant_id": data["tenant_id"],
                        "bronze_id": data["bronze_id"],
                        "document_type": data["document_type"],
                        "document_hash": data.get("document_hash", ""),
                        "file_name": data.get("file_name", ""),
                        "is_structured": data.get("is_structured", False),
                        "confidence_score": data.get("confidence_score"),
                        "extracted_data": extracted_str,
                        "raw_text": data.get("raw_text", ""),
                        "processed_at": data.get("processed_at", datetime.now(timezone.utc)),
                        "status": data.get("status", "processed"),
                    },
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao inserir envelope: {e}")
            return False

    def get_envelope_by_id(
        self, envelope_id: str, org_id: str, tenant_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT * FROM envelope_documents "
                        "WHERE id = :id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                    ),
                    {"id": envelope_id, "org_id": org_id, "tenant_id": tenant_id},
                ).fetchone()
                if row is not None:
                    data = dict(row._mapping)
                    if data.get("extracted_data"):
                        try:
                            data["extracted_data"] = json.loads(data["extracted_data"])
                        except Exception:
                            pass
                    return data
        except Exception as e:
            logger.error(f"Erro ao buscar envelope: {e}")
        return None

    def list_envelopes(
        self,
        org_id: str,
        tenant_id: Optional[str],
        document_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                query = (
                    "SELECT id, org_id, tenant_id, bronze_id, document_type, "
                    "file_name, is_structured, confidence_score, processed_at, status "
                    "FROM envelope_documents "
                    "WHERE org_id = :org_id AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                )
                params: Dict[str, Any] = {"org_id": org_id, "tenant_id": tenant_id}
                if document_type:
                    query += " AND document_type = :document_type"
                    params["document_type"] = document_type
                query += " ORDER BY processed_at DESC LIMIT :limit"
                params["limit"] = limit
                rows = session.execute(text(query), params).fetchall()
                return [dict(r._mapping) for r in rows]
        except Exception as e:
            logger.error(f"Erro ao listar envelopes: {e}")
            return []

    # ── Document Images ───────────────────────────────────────────

    def insert_document_image(self, data: Dict[str, Any]) -> bool:
        if not data.get("org_id"):
            return False
        try:
            with self.get_session() as session:
                json_fields = {
                    "vlm_tags": data.get("vlm_tags"),
                    "vlm_detected_objects": data.get("vlm_detected_objects"),
                    "vlm_technical_context": data.get("vlm_technical_context"),
                    "vlm_text_content": data.get("vlm_text_content"),
                    "vlm_quality_indicators": data.get("vlm_quality_indicators"),
                }
                serialized = {}
                for k, v in json_fields.items():
                    serialized[k] = json.dumps(v, ensure_ascii=False) if v else None

                session.execute(
                    text(
                        "INSERT INTO document_images "
                        "(id, org_id, tenant_id, bronze_id, page_number, image_index, "
                        "image_hash, object_path, width, height, format, file_size, "
                        "x_position, y_position, colorspace, page_context, extracted_at, "
                        "vlm_description, vlm_category, vlm_tags, vlm_detected_objects, "
                        "vlm_technical_context, vlm_text_content, vlm_quality_indicators, "
                        "vlm_confidence, status) "
                        "VALUES (:id, :org_id, :tenant_id, :bronze_id, :page_number, "
                        ":image_index, :image_hash, :object_path, :width, :height, :format, "
                        ":file_size, :x_position, :y_position, :colorspace, :page_context, "
                        ":extracted_at, :vlm_description, :vlm_category, :vlm_tags, "
                        ":vlm_detected_objects, :vlm_technical_context, :vlm_text_content, "
                        ":vlm_quality_indicators, :vlm_confidence, :status) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "vlm_description = EXCLUDED.vlm_description, "
                        "vlm_category = EXCLUDED.vlm_category, "
                        "vlm_tags = EXCLUDED.vlm_tags, "
                        "vlm_detected_objects = EXCLUDED.vlm_detected_objects, "
                        "vlm_technical_context = EXCLUDED.vlm_technical_context, "
                        "vlm_text_content = EXCLUDED.vlm_text_content, "
                        "vlm_quality_indicators = EXCLUDED.vlm_quality_indicators, "
                        "vlm_confidence = EXCLUDED.vlm_confidence, "
                        "status = EXCLUDED.status"
                    ),
                    {
                        "id": data["id"],
                        "org_id": data["org_id"],
                        "tenant_id": data["tenant_id"],
                        "bronze_id": data["bronze_id"],
                        "page_number": data.get("page_number"),
                        "image_index": data.get("image_index"),
                        "image_hash": data.get("image_hash"),
                        "object_path": data.get("object_path"),
                        "width": data.get("width"),
                        "height": data.get("height"),
                        "format": data.get("format"),
                        "file_size": data.get("file_size"),
                        "x_position": data.get("x_position"),
                        "y_position": data.get("y_position"),
                        "colorspace": data.get("colorspace"),
                        "page_context": data.get("page_context"),
                        "extracted_at": data.get("extracted_at", datetime.now(timezone.utc)),
                        "vlm_description": data.get("vlm_description"),
                        "vlm_category": data.get("vlm_category"),
                        **serialized,
                        "vlm_confidence": data.get("vlm_confidence"),
                        "status": data.get("status", "cataloged"),
                    },
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao inserir imagem: {e}")
            return False

    def get_document_images(
        self,
        org_id: str,
        tenant_id: Optional[str],
        bronze_id: Optional[str] = None,
        page_number: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                query = (
                    "SELECT * FROM document_images "
                    "WHERE org_id = :org_id AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                )
                params: Dict[str, Any] = {"org_id": org_id, "tenant_id": tenant_id}
                if bronze_id:
                    query += " AND bronze_id = :bronze_id"
                    params["bronze_id"] = bronze_id
                if page_number is not None:
                    query += " AND page_number = :page_number"
                    params["page_number"] = page_number
                query += " ORDER BY bronze_id, page_number, image_index LIMIT :limit"
                params["limit"] = limit
                rows = session.execute(text(query), params).fetchall()
                return [dict(r._mapping) for r in rows]
        except Exception as e:
            logger.error(f"Erro ao buscar imagens: {e}")
            return []

    def get_image_by_id(
        self, image_id: str, org_id: str, tenant_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT * FROM document_images "
                        "WHERE id = :id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                    ),
                    {"id": image_id, "org_id": org_id, "tenant_id": tenant_id},
                ).fetchone()
                if row is not None:
                    return dict(row._mapping)
        except Exception as e:
            logger.error(f"Erro ao buscar imagem: {e}")
        return None

    def count_document_images(
        self, org_id: str, tenant_id: Optional[str], bronze_id: Optional[str] = None
    ) -> int:
        try:
            with self.get_session() as session:
                query = (
                    "SELECT COUNT(*) FROM document_images "
                    "WHERE org_id = :org_id AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                )
                params: Dict[str, Any] = {"org_id": org_id, "tenant_id": tenant_id}
                if bronze_id:
                    query += " AND bronze_id = :bronze_id"
                    params["bronze_id"] = bronze_id
                result = session.execute(text(query), params).scalar()
                return int(result or 0)
        except Exception as e:
            logger.error(f"Erro ao contar imagens: {e}")
            return 0

    # ── Ingest Jobs ───────────────────────────────────────────────

    def create_ingest_job(
        self, job_id: str, org_id: str, tenant_id: Optional[str], file_name: Optional[str] = None
    ) -> bool:
        try:
            with self.get_session() as session:
                now = datetime.now(timezone.utc)
                session.execute(
                    text(
                        "INSERT INTO ingest_jobs "
                        "(id, org_id, tenant_id, file_name, status, created_at, updated_at) "
                        "VALUES (:id, :org_id, :tenant_id, :file_name, 'pending', :now, :now)"
                    ),
                    {"id": job_id, "org_id": org_id, "tenant_id": tenant_id, "file_name": file_name, "now": now},
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao criar job: {e}")
            return False

    def update_ingest_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        bronze_id: Optional[str] = None,
        envelope_id: Optional[str] = None,
        document_type: Optional[str] = None,
    ) -> bool:
        try:
            with self.get_session() as session:
                updates = ["updated_at = :now"]
                params: Dict[str, Any] = {"id": job_id, "now": datetime.now(timezone.utc)}
                if status:
                    updates.append("status = :status")
                    params["status"] = status
                if result is not None:
                    updates.append("result = :result")
                    params["result"] = json.dumps(result, ensure_ascii=False, default=str)
                if error:
                    updates.append("error = :error")
                    params["error"] = error
                if bronze_id:
                    updates.append("bronze_id = :bronze_id")
                    params["bronze_id"] = bronze_id
                if envelope_id:
                    updates.append("envelope_id = :envelope_id")
                    params["envelope_id"] = envelope_id
                if document_type:
                    updates.append("document_type = :document_type")
                    params["document_type"] = document_type
                session.execute(
                    text(f"UPDATE ingest_jobs SET {', '.join(updates)} WHERE id = :id"),
                    params,
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao atualizar job: {e}")
            return False

    def get_ingest_job(
        self, job_id: str, org_id: str, tenant_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT id, org_id, tenant_id, file_name, status, bronze_id, "
                        "envelope_id, document_type, result, error, created_at, updated_at "
                        "FROM ingest_jobs "
                        "WHERE id = :id AND org_id = :org_id "
                        "AND tenant_id IS NOT DISTINCT FROM :tenant_id"
                    ),
                    {"id": job_id, "org_id": org_id, "tenant_id": tenant_id},
                ).fetchone()
                if row is not None:
                    return dict(row._mapping)
        except Exception as e:
            logger.error(f"Erro ao buscar job: {e}")
        return None

    def recover_stale_work(self, stale_after_minutes: int = 30) -> Dict[str, int]:
        """Marca trabalho local interrompido para não ficar eternamente em running."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, stale_after_minutes))
        try:
            with self.get_session() as session:
                jobs = session.execute(
                    text(
                        "UPDATE ingest_jobs SET status = 'interrupted', "
                        "error = COALESCE(error, 'Processamento interrompido antes da conclusão.'), "
                        "updated_at = :now WHERE status IN ('pending', 'running') "
                        "AND updated_at < :cutoff"
                    ),
                    {"now": datetime.now(timezone.utc), "cutoff": cutoff},
                ).rowcount
                versions = session.execute(
                    text(
                        "UPDATE fault_tree_versions SET status = 'failed', "
                        "error = COALESCE(error, 'Geração interrompida antes da conclusão.'), "
                        "updated_at = :now WHERE status IN ('queued', 'running') "
                        "AND updated_at < :cutoff"
                    ),
                    {"now": datetime.now(timezone.utc), "cutoff": cutoff},
                ).rowcount
                session.execute(
                    text(
                        "UPDATE fault_trees SET generation_status = 'failed', "
                        "generation_error = 'Geração interrompida antes da conclusão.', "
                        "generation_updated_at = :now WHERE generation_status IN ('queued', 'running') "
                        "AND generation_updated_at < :cutoff"
                    ),
                    {"now": datetime.now(timezone.utc), "cutoff": cutoff},
                )
                session.commit()
                return {"ingest_jobs": int(jobs or 0), "generations": int(versions or 0)}
        except Exception as e:
            logger.error(f"Erro ao recuperar trabalhos interrompidos: {e}")
            return {"ingest_jobs": 0, "generations": 0}

    # ── Document Bindings ─────────────────────────────────────────

    def bind_document(
        self,
        *,
        document_id: str,
        envelope_id: Optional[str],
        org_id: str,
        tenant_id: Optional[str],
        target_type: str,
        target_id: str,
        model_id: Optional[str] = None,
    ) -> bool:
        if target_type not in {"model", "equipment"}:
            return False
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "INSERT INTO document_bindings "
                        "(id, document_id, envelope_id, org_id, tenant_id, target_type, "
                        "target_id, model_id, created_at) "
                        "VALUES (:id, :document_id, :envelope_id, :org_id, :tenant_id, "
                        ":target_type, :target_id, :model_id, :created_at) "
                        "ON CONFLICT ON CONSTRAINT uq_document_binding_tenant_scoped DO UPDATE SET "
                        "envelope_id = EXCLUDED.envelope_id, "
                        "model_id = EXCLUDED.model_id"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "document_id": document_id,
                        "envelope_id": envelope_id,
                        "org_id": org_id,
                        "tenant_id": tenant_id,
                        "target_type": target_type,
                        "target_id": target_id,
                        "model_id": model_id,
                        "created_at": datetime.now(timezone.utc),
                    },
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao associar documento ao alvo: {e}")
            return False

    def get_document_binding(
        self,
        *,
        document_id: str,
        org_id: str,
        tenant_id: Optional[str],
        target_type: str,
        target_id: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT * FROM document_bindings WHERE document_id = :document_id "
                        "AND org_id = :org_id AND tenant_id IS NOT DISTINCT FROM :tenant_id "
                        "AND target_type = :target_type AND target_id = :target_id LIMIT 1"
                    ),
                    {
                        "document_id": document_id,
                        "org_id": org_id,
                        "tenant_id": tenant_id,
                        "target_type": target_type,
                        "target_id": target_id,
                    },
                ).fetchone()
                return dict(row._mapping) if row is not None else None
        except Exception as e:
            logger.error(f"Erro ao buscar vínculo de documento: {e}")
            return None

    def list_document_bindings(
        self,
        *,
        org_id: str,
        tenant_id: Optional[str],
        target_type: str,
        target_id: str,
    ) -> List[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                rows = session.execute(
                    text(
                        "SELECT b.*, d.tenant_id AS document_tenant_id, d.file_name, d.file_type, d.object_path, "
                        "d.status AS document_status, CASE WHEN d.tenant_id IS DISTINCT FROM b.tenant_id "
                        "THEN 'inherited' ELSE 'source' END AS relationship, "
                        "d.upload_date FROM document_bindings b "
                        "LEFT JOIN bronze_documents d ON d.id = b.document_id "
                        "WHERE b.org_id = :org_id "
                        "AND b.tenant_id IS NOT DISTINCT FROM :tenant_id "
                        "AND b.target_type = :target_type AND b.target_id = :target_id "
                        "ORDER BY b.created_at DESC"
                    ),
                    {
                        "org_id": org_id,
                        "tenant_id": tenant_id,
                        "target_type": target_type,
                        "target_id": target_id,
                    },
                ).fetchall()
                return [dict(row._mapping) for row in rows]
        except Exception as e:
            logger.error(f"Erro ao listar vínculos de documentos: {e}")
            return []

    def copy_model_document_bindings(
        self, *, model_id: str, equipment_id: str, org_id: str, tenant_id: str
    ) -> List[Dict[str, Any]]:
        source = self.list_document_bindings(
            org_id=org_id, tenant_id=None, target_type="model", target_id=model_id
        )
        copied: List[Dict[str, Any]] = []
        for binding in source:
            if self.bind_document(
                document_id=binding["document_id"],
                envelope_id=binding.get("envelope_id"),
                org_id=org_id,
                tenant_id=tenant_id,
                target_type="equipment",
                target_id=equipment_id,
                model_id=model_id,
            ):
                copied.append(binding)
        return copied

    # ── Processing Logs ───────────────────────────────────────────

    # ── Fault Trees ───────────────────────────────────────────────

    def create_fault_tree(self, data: Dict[str, Any]) -> bool:
        try:
            with self.get_session() as session:
                now = datetime.now(timezone.utc)
                tree_data = data.get("tree_data")
                failure_modes = data.get("failure_modes")
                session.execute(
                    text(
                        "INSERT INTO fault_trees "
                        "(id, org_id, model_id, tenant_id, equipment_id, tree_data, failure_modes, created_at, updated_at) "
                        "VALUES (:id, :org_id, :model_id, :tenant_id, :equipment_id, :tree_data, :failure_modes, :now, :now)"
                    ),
                    {
                        "id": data["id"],
                        "org_id": data["org_id"],
                        "model_id": data.get("model_id"),
                        "tenant_id": data.get("tenant_id"),
                        "equipment_id": data.get("equipment_id"),
                        "tree_data": json.dumps(tree_data, ensure_ascii=False) if isinstance(tree_data, (dict, list)) else tree_data,
                        "failure_modes": json.dumps(failure_modes, ensure_ascii=False) if isinstance(failure_modes, (dict, list)) else failure_modes,
                        "now": now,
                    },
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao criar fault tree: {e}")
            return False

    def _parse_fault_tree_row(self, data: Dict[str, Any]) -> Dict[str, Any]:
        for field in ("tree_data", "failure_modes"):
            raw = data.get(field)
            if raw and isinstance(raw, str):
                try:
                    data[field] = json.loads(raw)
                except Exception:
                    pass
        return data

    def get_fault_tree_by_model(self, model_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT * FROM fault_trees "
                        "WHERE org_id = :org_id AND model_id = :model_id "
                        "AND equipment_id IS NULL AND tenant_id IS NULL "
                        "LIMIT 1"
                    ),
                    {"org_id": org_id, "model_id": model_id},
                ).fetchone()
                if row is not None:
                    return self._parse_fault_tree_row(dict(row._mapping))
        except Exception as e:
            logger.error(f"Erro ao buscar fault tree por modelo: {e}")
        return None

    def get_equipment_fault_tree(
        self,
        *,
        org_id: str,
        tenant_id: str,
        equipment_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Consulta tenant-scoped. tenant_id é obrigatório e sempre incluído no filtro SQL.

        Keyword-only: é impossível passar tenant_id como posicional ou omiti-lo
        acidentalmente — Python levanta TypeError antes de chegar ao banco.
        """
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT * FROM fault_trees "
                        "WHERE org_id = :org_id "
                        "AND tenant_id = :tenant_id "
                        "AND equipment_id = :equipment_id "
                        "LIMIT 1"
                    ),
                    {"org_id": org_id, "tenant_id": tenant_id, "equipment_id": equipment_id},
                ).fetchone()
                if row is not None:
                    return self._parse_fault_tree_row(dict(row._mapping))
        except Exception as e:
            logger.error(f"Erro ao buscar fault tree por equipamento: {e}")
        return None

    def get_fault_tree_by_equipment(
        self, equipment_id: str, org_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Mantido para compatibilidade de migração.

        Levanta ValueError se tenant_id for omitido ou None — elimina o fallback
        silencioso para consulta cross-tenant. Prefira get_equipment_fault_tree().
        """
        if not tenant_id:
            raise ValueError(
                "tenant_id é obrigatório. Use get_equipment_fault_tree() com keyword args. "
                "Passar tenant_id=None causaria consulta cross-tenant."
            )
        return self.get_equipment_fault_tree(
            org_id=org_id, tenant_id=tenant_id, equipment_id=equipment_id
        )

    def update_fault_tree(self, tree_id: str, org_id: str, tree_data: Any) -> bool:
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "UPDATE fault_trees SET tree_data = :tree_data, updated_at = :now "
                        "WHERE id = :id AND org_id = :org_id"
                    ),
                    {
                        "id": tree_id,
                        "org_id": org_id,
                        "tree_data": json.dumps(tree_data, ensure_ascii=False) if isinstance(tree_data, dict) else tree_data,
                        "now": datetime.now(timezone.utc),
                    },
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao atualizar fault tree: {e}")
            return False

    def delete_fault_tree(self, tree_id: str, org_id: str) -> bool:
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "DELETE FROM fault_tree_version_documents WHERE version_id IN "
                        "(SELECT id FROM fault_tree_versions WHERE tree_id = :id AND org_id = :org_id)"
                    ),
                    {"id": tree_id, "org_id": org_id},
                )
                session.execute(
                    text(
                        "DELETE FROM fault_tree_versions WHERE tree_id = :id AND org_id = :org_id"
                    ),
                    {"id": tree_id, "org_id": org_id},
                )
                session.execute(
                    text("DELETE FROM fault_trees WHERE id = :id AND org_id = :org_id"),
                    {"id": tree_id, "org_id": org_id},
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao deletar fault tree: {e}")
            return False

    def update_fault_tree_generation_status(
        self, tree_id: str, org_id: str, status: str, error: Optional[str] = None
    ) -> bool:
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "UPDATE fault_trees SET generation_status = :status, "
                        "generation_error = :error, generation_updated_at = :now "
                        "WHERE id = :id AND org_id = :org_id"
                    ),
                    {"id": tree_id, "org_id": org_id, "status": status, "error": error, "now": datetime.now(timezone.utc)},
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao atualizar generation_status: {e}")
            return False

    def update_fault_tree_failure_modes(
        self, tree_id: str, org_id: str, failure_modes: Any
    ) -> bool:
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "UPDATE fault_trees SET failure_modes = :fm, generation_status = 'completed', "
                        "generation_updated_at = :now, updated_at = :now "
                        "WHERE id = :id AND org_id = :org_id"
                    ),
                    {
                        "id": tree_id,
                        "org_id": org_id,
                        "fm": json.dumps(failure_modes, ensure_ascii=False) if not isinstance(failure_modes, str) else failure_modes,
                        "now": datetime.now(timezone.utc),
                    },
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao atualizar failure_modes: {e}")
            return False

    # ── Fault Tree Versions ───────────────────────────────────────

    def create_fault_tree_version(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        version_id = data.get("id") or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "INSERT INTO fault_tree_versions "
                        "(id, tree_id, org_id, tenant_id, model_id, equipment_id, document_id, "
                        "envelope_id, file_name, status, failure_modes, error, "
                        "supersedes_version_id, created_at, updated_at, activated_at) "
                        "VALUES (:id, :tree_id, :org_id, :tenant_id, :model_id, :equipment_id, :document_id, "
                        ":envelope_id, :file_name, :status, :failure_modes, :error, "
                        ":supersedes_version_id, :now, :now, :activated_at)"
                    ),
                    {
                        "id": version_id,
                        "tree_id": data["tree_id"],
                        "org_id": data["org_id"],
                        "tenant_id": data.get("tenant_id"),
                        "model_id": data.get("model_id"),
                        "equipment_id": data.get("equipment_id"),
                        "document_id": data.get("document_id"),
                        "envelope_id": data.get("envelope_id"),
                        "file_name": data.get("file_name"),
                        "status": data.get("status", "queued"),
                        "failure_modes": json.dumps(data.get("failure_modes"), ensure_ascii=False) if data.get("failure_modes") is not None else None,
                        "error": data.get("error"),
                        "supersedes_version_id": data.get("supersedes_version_id"),
                        "now": now,
                        "activated_at": now if data.get("status") == "active" else None,
                    },
                )
                session.commit()
            return self.get_fault_tree_version(version_id, data["org_id"])
        except Exception as e:
            logger.error(f"Erro ao criar versão da árvore: {e}")
            return None

    def link_version_document(
        self,
        *,
        version_id: str,
        document_id: str,
        envelope_id: Optional[str],
        relationship: str,
        origin_type: str,
        origin_id: str,
    ) -> bool:
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "INSERT INTO fault_tree_version_documents "
                        "(id, version_id, document_id, envelope_id, relationship, origin_type, origin_id, created_at) "
                        "VALUES (:id, :version_id, :document_id, :envelope_id, :relationship, "
                        ":origin_type, :origin_id, :created_at) ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "version_id": version_id,
                        "document_id": document_id,
                        "envelope_id": envelope_id,
                        "relationship": relationship,
                        "origin_type": origin_type,
                        "origin_id": origin_id,
                        "created_at": datetime.now(timezone.utc),
                    },
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao vincular documento à versão: {e}")
            return False

    def get_active_fault_tree_version(self, tree_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        """Retorna a versão atualmente 'active' de uma árvore, ou None."""
        try:
            with self.get_session() as session:
                row = session.execute(
                    text(
                        "SELECT id, tree_id, org_id, status FROM fault_tree_versions "
                        "WHERE tree_id = :tree_id AND org_id = :org_id AND status = 'active' "
                        "LIMIT 1"
                    ),
                    {"tree_id": tree_id, "org_id": org_id},
                ).fetchone()
                if row is None:
                    return None
                return dict(row._mapping)
        except Exception as e:
            logger.error(f"Erro ao buscar versão ativa da árvore: {e}")
            return None

    def get_fault_tree_version(self, version_id: str, org_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                row = session.execute(
                    text("SELECT * FROM fault_tree_versions WHERE id = :id AND org_id = :org_id"),
                    {"id": version_id, "org_id": org_id},
                ).fetchone()
                if row is None:
                    return None
                data = dict(row._mapping)
                if data.get("failure_modes") and isinstance(data["failure_modes"], str):
                    data["failure_modes"] = json.loads(data["failure_modes"])
                return data
        except Exception as e:
            logger.error(f"Erro ao buscar versão da árvore: {e}")
            return None

    def list_fault_tree_versions(self, tree_id: str, org_id: str) -> List[Dict[str, Any]]:
        try:
            with self.get_session() as session:
                rows = session.execute(
                    text(
                        "SELECT * FROM fault_tree_versions WHERE tree_id = :tree_id "
                        "AND org_id = :org_id ORDER BY created_at DESC"
                    ),
                    {"tree_id": tree_id, "org_id": org_id},
                ).fetchall()
                result = []
                for row in rows:
                    data = dict(row._mapping)
                    if data.get("failure_modes") and isinstance(data["failure_modes"], str):
                        data["failure_modes"] = json.loads(data["failure_modes"])
                    result.append(data)
                return result
        except Exception as e:
            logger.error(f"Erro ao listar versões da árvore: {e}")
            return []

    def update_fault_tree_version_status(
        self, version_id: str, org_id: str, status: str, error: Optional[str] = None
    ) -> bool:
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "UPDATE fault_tree_versions SET status = :status, error = :error, updated_at = :now "
                        "WHERE id = :id AND org_id = :org_id"
                    ),
                    {"id": version_id, "org_id": org_id, "status": status, "error": error, "now": datetime.now(timezone.utc)},
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao atualizar versão da árvore: {e}")
            return False

    def update_fault_tree_version_progress(
        self, version_id: str, org_id: str, percent: int, phase: str, message: str
    ) -> None:
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "UPDATE fault_tree_versions "
                        "SET progress = :p, progress_phase = :phase, progress_message = :msg, updated_at = :now "
                        "WHERE id = :id AND org_id = :org_id"
                    ),
                    {
                        "id": version_id, "org_id": org_id,
                        "p": max(0, min(99, int(percent))),
                        "phase": phase, "msg": message,
                        "now": datetime.now(timezone.utc),
                    },
                )
                session.commit()
        except Exception as e:
            logger.debug(f"Progress update ignorado (coluna ausente?): {e}")

    def activate_fault_tree_version(
        self,
        version_id: str,
        tree_id: str,
        org_id: str,
        failure_modes: Any,
        supersedes_version_id: Optional[str] = None,
    ) -> bool:
        """Promove a candidata com Compare-And-Swap para evitar stale promotion.

        supersedes_version_id: id da versão que estava 'active' quando esta geração
        foi criada. Se essa versão específica já não estiver 'active' (outra geração
        concorrente terminou primeiro), retorna False sem promover.

        Se None (primeira geração), verifica que não existe nenhuma versão ativa.
        """
        now = datetime.now(timezone.utc)
        serialized = json.dumps(failure_modes, ensure_ascii=False)
        try:
            with self.get_session() as session:
                if supersedes_version_id is not None:
                    # CAS: supersede apenas a versão específica que esperávamos
                    supersede_result = session.execute(
                        text(
                            "UPDATE fault_tree_versions SET status = 'superseded', updated_at = :now "
                            "WHERE id = :sid AND tree_id = :tree_id AND org_id = :org_id AND status = 'active'"
                        ),
                        {"sid": supersedes_version_id, "tree_id": tree_id, "org_id": org_id, "now": now},
                    )
                    if supersede_result.rowcount != 1:
                        session.rollback()
                        logger.warning(
                            f"Ativação abortada (versão {version_id}): versão esperada "
                            f"'{supersedes_version_id}' já não está ativa — geração concorrente ganhou."
                        )
                        return False
                else:
                    # Primeira geração: garante que nenhuma versão ativa foi criada
                    # por uma geração concorrente enquanto esta estava rodando
                    existing = session.execute(
                        text(
                            "SELECT id FROM fault_tree_versions "
                            "WHERE tree_id = :tree_id AND org_id = :org_id AND status = 'active'"
                        ),
                        {"tree_id": tree_id, "org_id": org_id},
                    ).fetchone()
                    if existing is not None:
                        session.rollback()
                        logger.warning(
                            f"Ativação abortada (versão {version_id}): "
                            "geração concorrente criou versão ativa antes desta concluir."
                        )
                        return False

                candidate_update = session.execute(
                    text(
                        "UPDATE fault_tree_versions SET status = 'active', failure_modes = :fm, "
                        "error = NULL, updated_at = :now, activated_at = :now "
                        "WHERE id = :id AND tree_id = :tree_id AND org_id = :org_id"
                    ),
                    {"id": version_id, "tree_id": tree_id, "org_id": org_id, "fm": serialized, "now": now},
                )
                tree_update = session.execute(
                    text(
                        "UPDATE fault_trees SET failure_modes = :fm, generation_status = 'completed', "
                        "generation_error = NULL, generation_updated_at = :now, updated_at = :now "
                        "WHERE id = :tree_id AND org_id = :org_id"
                    ),
                    {"tree_id": tree_id, "org_id": org_id, "fm": serialized, "now": now},
                )
                if candidate_update.rowcount != 1 or tree_update.rowcount != 1:
                    session.rollback()
                    return False
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao ativar versão da árvore: {e}")
            return False

    # ── Processing Logs ───────────────────────────────────────────

    def log_processing(self, data: Dict[str, Any]) -> bool:
        if not data.get("org_id") or not data.get("tenant_id"):
            return False
        try:
            with self.get_session() as session:
                session.execute(
                    text(
                        "INSERT INTO processing_logs "
                        "(id, org_id, tenant_id, document_id, layer, operation, "
                        "status, error_message, metadata, timestamp) "
                        "VALUES (:id, :org_id, :tenant_id, :document_id, :layer, "
                        ":operation, :status, :error_message, :metadata, :timestamp)"
                    ),
                    {
                        "id": data.get("id", str(uuid.uuid4())),
                        "org_id": data["org_id"],
                        "tenant_id": data["tenant_id"],
                        "document_id": data.get("document_id"),
                        "layer": data.get("layer"),
                        "operation": data.get("operation"),
                        "status": data.get("status"),
                        "error_message": data.get("error_message"),
                        "metadata": data.get("metadata"),
                        "timestamp": datetime.now(timezone.utc),
                    },
                )
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Erro ao registrar log: {e}")
            return False


postgres_client = PostgresClient()
