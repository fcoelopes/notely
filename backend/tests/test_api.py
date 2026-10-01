from __future__ import annotations

from uuid import uuid4

import httpx

from notely.api.app import create_app
from notely.core.models import Annotation, AnnotationType


class StubService:
    def __init__(self, annotation: Annotation | None = None, error: Exception | None = None) -> None:
        self.annotation = annotation
        self.error = error

    async def create_annotation(self, **_: object) -> Annotation:
        if self.error is not None:
            raise self.error
        assert self.annotation is not None
        return self.annotation


async def test_create_annotation_contract_preserves_user_provenance() -> None:
    document_id = uuid4()
    annotation = Annotation(
        document_id=document_id,
        page_number=1,
        type=AnnotationType.HIGHLIGHT,
        quote="A selected passage",
        position={"rects": [{"x": 1, "y": 2, "width": 3, "height": 4}]},
    )
    app = create_app(lambda: StubService(annotation))  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/annotations",
            json={
                "document_id": str(document_id),
                "page_number": 1,
                "type": "highlight",
                "quote": "A selected passage",
                "position": {"rects": [{"x": 1, "y": 2, "width": 3, "height": 4}]},
            },
        )

    assert response.status_code == 201
    assert response.json()["author_type"] == "user"
    assert response.json()["source"] == "user_selection"


async def test_domain_validation_is_returned_as_unprocessable_entity() -> None:
    app = create_app(lambda: StubService(error=ValueError("position is required")))  # type: ignore[arg-type]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/annotations",
            json={
                "document_id": str(uuid4()),
                "page_number": 1,
                "type": "highlight",
                "quote": "A selected passage",
                "position": {},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "position is required"
