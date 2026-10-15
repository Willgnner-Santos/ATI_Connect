import asyncio
import json
from pathlib import Path

from fastapi import BackgroundTasks

from src import api
from src.auth.jwt_validator import TenantContext


class _PdfUpload:
    filename = "manual.pdf"

    def __init__(self) -> None:
        self._chunks = [b"%PDF-1.4\n", b""]

    async def read(self, _: int) -> bytes:
        return self._chunks.pop(0)


def test_equipment_upload_accepts_missing_model(monkeypatch):
    created_jobs = []
    monkeypatch.setattr(
        api.db,
        "create_ingest_job",
        lambda *args, **kwargs: created_jobs.append((args, kwargs)) or True,
    )
    background = BackgroundTasks()
    equipment_id = "bca0d293-7e25-4489-832d-e4cf0ba8b42c"
    tenant = TenantContext(
        org_id="org-1",
        tenant_id="tenant-1",
        organization_role="superadmin",
        tenant_role=None,
        sub="user-1",
        raw_attrs={},
    )

    response = asyncio.run(api.ingest_file(
        file=_PdfUpload(),
        context_ids=None,
        target_type="equipment",
        target_id=equipment_id,
        model_id=None,
        generate_fault_tree=False,
        background_tasks=background,
        tenant=tenant,
    ))

    payload = json.loads(response.body)
    assert response.status_code == 202
    assert payload["target_id"] == equipment_id
    assert created_jobs[0][1]["tenant_id"] == "tenant-1"
    task = background.tasks[0]
    assert task.args[8] is None
    Path(task.args[1]).unlink(missing_ok=True)
