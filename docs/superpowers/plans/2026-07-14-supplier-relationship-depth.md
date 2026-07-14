# Supplier Relationship Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `Supplier` contacts, a structured address, payment/lead-time terms, freeform tags, and a computed on-time/defect scorecard; extend receiving to track rejected quantity so the scorecard has data to compute from.

**Architecture:** Additive changes only - one new table (`supplier_contacts`), new nullable/defaulted columns on `suppliers` and `purchase_order_receipt_lines`, new endpoints under the existing `/suppliers` router, and a small extension of the existing `receive_purchase_order` write path. No existing endpoint contract changes shape (only adds optional fields), so nothing already shipped breaks.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (backend), React + TanStack Query + Tailwind (frontend), pytest + httpx `AsyncClient` against an in-memory SQLite DB (backend tests), no frontend test runner (verify via `tsc` + `eslint`).

## Global Constraints

- Backend models must compile under **both** SQLite (used by the `client`/`test_engine` pytest fixtures - see `backend/tests/conftest.py`) and Postgres (production, and the `pg_client`-based concurrency tests). Concretely: **do not use `sqlalchemy.ARRAY` or `postgresql.ARRAY`** - SQLite can't compile array DDL and `Base.metadata.create_all` would fail for the whole suite. Use `JSON` for list-valued columns, matching the existing `Kpi.tags` precedent (`backend/app/models/kpi.py:42`).
- Every mutating endpoint follows the existing pattern in this file set: `get_or_404` for lookups, `require_role("owner")` for writes, a `log_event(...)` audit entry before `commit`, `await db.flush()` before the audit log, single `await db.commit()` at the end.
- Money/quantity fields are `Decimal` via `Numeric(12, 4)`, never `float`, matching every existing quantity column in `procurement.py`.
- New migration's `down_revision` is `a4f7c2e91b58` (confirmed via `python -m alembic heads` - it's the current single head).

---

### Task 1: Migration + model changes

**Files:**
- Modify: `backend/app/models/procurement.py`
- Create: `backend/alembic/versions/c8a1e5f93d64_supplier_relationship_depth.py`
- Test: `backend/tests/test_procurement.py` (existing tests double as the regression check - see Step 4)

**Interfaces:**
- Produces: `Supplier.address_line1/address_line2/city/state/postal_code/country: str | None`, `Supplier.payment_terms_days/lead_time_days: int | None`, `Supplier.tags: list[str]` (default `[]`); new model `SupplierContact` with `id, org_id, supplier_id, name, role, email, phone, is_primary`; `PurchaseOrderReceiptLine.quantity_rejected: Decimal` (default `0`), `PurchaseOrderReceiptLine.reject_reason: str | None`. All later tasks import `SupplierContact` from `app.models.procurement` and read/write these columns.

- [ ] **Step 1: Update the SQLAlchemy models**

Open `backend/app/models/procurement.py`. Replace the top-of-file `sqlalchemy` import (currently a single line: `from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, Uuid`) with:

```python
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
```

In the `Supplier` class, insert the following new columns directly after the existing `is_active` column (which stays where it is) and before `created_at`:

```python
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Expected days from PO submit to delivery - used to default a new PO's
    # expected_delivery_date when the caller doesn't supply one. See
    # app.api.routes.purchase_orders.create_purchase_order and
    # app.api.routes.forecasts._new_draft_po.
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Freeform, user-typed - same JSON-list convention as Kpi.tags
    # (app.models.kpi). No dedicated array type: SQLite (used by the
    # in-memory test suite) can't compile a native array column.
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
```

Immediately after the `Supplier` class (before `class PurchaseOrder(Base):`), add a new class:

```python
class SupplierContact(Base):
    __tablename__ = "supplier_contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Not DB-enforced as unique-per-supplier (a partial unique index would
    # need its own transaction to swap primaries atomically). The API
    # enforces "at most one primary" by unsetting any existing primary in
    # the same transaction when a new one is marked - see
    # app.api.routes.suppliers._unset_existing_primary.
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

In the `PurchaseOrderReceiptLine` class, add two columns at the end of the class body (after `received_at`):

```python
    # Portion of `quantity` that failed inspection - not additional to it.
    # Only quantity - quantity_rejected is applied to inventory/lot stock
    # (see receive_purchase_order); the full `quantity` still counts toward
    # PurchaseOrderLine.quantity_received, closing out the ordered amount -
    # a rejected unit still closes the line, a replacement is a new PO.
    quantity_rejected: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal(0))
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

- [ ] **Step 2: Write the migration**

Create `backend/alembic/versions/c8a1e5f93d64_supplier_relationship_depth.py`:

```python
"""supplier relationship depth

Revision ID: c8a1e5f93d64
Revises: a4f7c2e91b58
Create Date: 2026-07-14

"""
from alembic import op
import sqlalchemy as sa

revision = "c8a1e5f93d64"  # pragma: allowlist secret
down_revision = "a4f7c2e91b58"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("address_line1", sa.String(255), nullable=True))
    op.add_column("suppliers", sa.Column("address_line2", sa.String(255), nullable=True))
    op.add_column("suppliers", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("suppliers", sa.Column("state", sa.String(100), nullable=True))
    op.add_column("suppliers", sa.Column("postal_code", sa.String(20), nullable=True))
    op.add_column("suppliers", sa.Column("country", sa.String(100), nullable=True))
    op.add_column("suppliers", sa.Column("payment_terms_days", sa.Integer(), nullable=True))
    op.add_column("suppliers", sa.Column("lead_time_days", sa.Integer(), nullable=True))
    op.add_column(
        "suppliers", sa.Column("tags", sa.JSON(), nullable=False, server_default="[]")
    )

    op.create_table(
        "supplier_contacts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", sa.Uuid(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "supplier_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(100), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_supplier_contacts_org_id", "supplier_contacts", ["org_id"])
    op.create_index("ix_supplier_contacts_supplier_id", "supplier_contacts", ["supplier_id"])

    op.add_column(
        "purchase_order_receipt_lines",
        sa.Column("quantity_rejected", sa.Numeric(12, 4), nullable=False, server_default="0"),
    )
    op.add_column(
        "purchase_order_receipt_lines", sa.Column("reject_reason", sa.String(500), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("purchase_order_receipt_lines", "reject_reason")
    op.drop_column("purchase_order_receipt_lines", "quantity_rejected")

    op.drop_index("ix_supplier_contacts_supplier_id", table_name="supplier_contacts")
    op.drop_index("ix_supplier_contacts_org_id", table_name="supplier_contacts")
    op.drop_table("supplier_contacts")

    op.drop_column("suppliers", "tags")
    op.drop_column("suppliers", "lead_time_days")
    op.drop_column("suppliers", "payment_terms_days")
    op.drop_column("suppliers", "country")
    op.drop_column("suppliers", "postal_code")
    op.drop_column("suppliers", "state")
    op.drop_column("suppliers", "city")
    op.drop_column("suppliers", "address_line2")
    op.drop_column("suppliers", "address_line1")
```

- [ ] **Step 3: Verify the migration chain and model import both work**

Run: `cd backend && python -m alembic heads`
Expected: `c8a1e5f93d64 (head)` (single head, no branching)

Run: `cd backend && python -c "from app.models import procurement; print(procurement.SupplierContact.__tablename__)"`
Expected: `supplier_contacts` printed, no import error

- [ ] **Step 4: Run the existing test suite to confirm the new schema doesn't break anything**

Run: `cd backend && python -m pytest tests/test_procurement.py tests/test_supplier_lifecycle.py -q`
Expected: all existing tests still PASS (new columns are nullable/defaulted, so existing `Supplier(...)`/`PurchaseOrderReceiptLine(...)` construction calls elsewhere in the codebase are unaffected)

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/procurement.py backend/alembic/versions/c8a1e5f93d64_supplier_relationship_depth.py
git commit -m "feat: add supplier contacts, address/terms/tags, and receipt-line rejection columns"
```

---

### Task 2: Supplier contacts CRUD (backend)

**Files:**
- Modify: `backend/app/api/routes/suppliers.py`
- Test: `backend/tests/test_supplier_lifecycle.py`

**Interfaces:**
- Consumes: `SupplierContact` model (Task 1), `get_or_404(db, model, org_id, obj_id, not_found_msg, *extra_filters, for_update=False)` (`backend/app/api/deps.py:63`), `log_event(db, *, action, org_id, actor_user_id, target, metadata)` (`backend/app/middleware/audit.py:10`).
- Produces: `POST/GET /suppliers/{supplier_id}/contacts`, `PATCH/DELETE /suppliers/{supplier_id}/contacts/{contact_id}`. Response shape `{id, name, role, email, phone, is_primary}` - Task 7 (frontend) consumes this exact shape as `SupplierContactData`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_supplier_lifecycle.py`:

```python
async def test_supplier_contacts_crud_and_primary_swap(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    c1 = await client.post(
        f"/suppliers/{supplier['id']}/contacts",
        json={"name": "Jane Doe", "role": "Sales Rep", "email": "jane@vendor.test", "is_primary": True},
        headers=headers,
    )
    assert c1.status_code == 201
    c1_id = c1.json()["id"]
    assert c1.json()["is_primary"] is True

    c2 = await client.post(
        f"/suppliers/{supplier['id']}/contacts",
        json={"name": "Bob Smith", "role": "AP", "is_primary": True},
        headers=headers,
    )
    assert c2.status_code == 201
    c2_id = c2.json()["id"]
    assert c2.json()["is_primary"] is True

    listing = await client.get(f"/suppliers/{supplier['id']}/contacts", headers=headers)
    by_id = {c["id"]: c for c in listing.json()}
    # Marking c2 primary must have unset c1's primary flag.
    assert by_id[c1_id]["is_primary"] is False
    assert by_id[c2_id]["is_primary"] is True

    updated = await client.patch(
        f"/suppliers/{supplier['id']}/contacts/{c1_id}",
        json={"name": "Jane D.", "role": "Sales Rep", "is_primary": False},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Jane D."

    deleted = await client.delete(f"/suppliers/{supplier['id']}/contacts/{c2_id}", headers=headers)
    assert deleted.status_code == 204

    listing2 = await client.get(f"/suppliers/{supplier['id']}/contacts", headers=headers)
    assert [c["id"] for c in listing2.json()] == [c1_id]


async def test_supplier_contact_mutations_require_owner(client, test_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models.user import User

    token = await _register_and_login(client, email="owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    org_id = (await client.get("/orgs/me", headers=headers)).json()["id"]

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        import uuid as uuid_module

        session.add(
            User(
                org_id=uuid_module.UUID(org_id),
                email="member3@example.com",
                password_hash=hash_password(PASSWORD),
                role="member",
                is_active=True,
            )
        )
        await session.commit()

    login = await client.post("/auth/login", json={"email": "member3@example.com", "password": PASSWORD})
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    res = await client.post(
        f"/suppliers/{supplier['id']}/contacts", json={"name": "X"}, headers=member_headers
    )
    assert res.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_supplier_lifecycle.py -k "contact" -v`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Implement the contacts endpoints**

In `backend/app/api/routes/suppliers.py`, update the top imports: add `update` to the `sqlalchemy` import (`from sqlalchemy import select` becomes `from sqlalchemy import select, update`), and add `SupplierContact` to the model import (`from app.models.procurement import Supplier` becomes `from app.models.procurement import Supplier, SupplierContact`).

Append to the end of the file (after `reactivate_supplier`):

```python
class SupplierContactRequest(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    is_primary: bool = False


class SupplierContactResponse(BaseModel):
    id: str
    name: str
    role: str | None
    email: str | None
    phone: str | None
    is_primary: bool


def _contact_to_response(contact: SupplierContact) -> SupplierContactResponse:
    return SupplierContactResponse(
        id=str(contact.id),
        name=contact.name,
        role=contact.role,
        email=contact.email,
        phone=contact.phone,
        is_primary=contact.is_primary,
    )


async def _unset_existing_primary(db: AsyncSession, org_id: uuid.UUID, supplier_id: uuid.UUID) -> None:
    await db.execute(
        update(SupplierContact)
        .where(
            SupplierContact.org_id == org_id,
            SupplierContact.supplier_id == supplier_id,
            SupplierContact.is_primary.is_(True),
        )
        .values(is_primary=False)
    )


@router.get("/{supplier_id}/contacts", response_model=list[SupplierContactResponse])
async def list_supplier_contacts(
    supplier_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")
    result = await db.execute(
        select(SupplierContact)
        .where(SupplierContact.org_id == current.org_id, SupplierContact.supplier_id == supplier_id)
        .order_by(SupplierContact.is_primary.desc(), SupplierContact.name)
    )
    return [_contact_to_response(c) for c in result.scalars().all()]


@router.post(
    "/{supplier_id}/contacts",
    response_model=SupplierContactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier_contact(
    supplier_id: uuid.UUID,
    body: SupplierContactRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")
    if body.is_primary:
        await _unset_existing_primary(db, current.org_id, supplier_id)

    contact = SupplierContact(
        org_id=current.org_id,
        supplier_id=supplier_id,
        name=body.name,
        role=body.role,
        email=body.email,
        phone=body.phone,
        is_primary=body.is_primary,
    )
    db.add(contact)
    await db.flush()

    await log_event(
        db,
        action="supplier.contact_created",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(contact.id),
        metadata={"supplier_id": str(supplier_id), "name": contact.name},
    )
    await db.commit()
    await db.refresh(contact)
    return _contact_to_response(contact)


@router.patch("/{supplier_id}/contacts/{contact_id}", response_model=SupplierContactResponse)
async def update_supplier_contact(
    supplier_id: uuid.UUID,
    contact_id: uuid.UUID,
    body: SupplierContactRequest,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    contact = await get_or_404(
        db,
        SupplierContact,
        current.org_id,
        contact_id,
        "Contact not found",
        SupplierContact.supplier_id == supplier_id,
    )
    if body.is_primary and not contact.is_primary:
        await _unset_existing_primary(db, current.org_id, supplier_id)

    contact.name = body.name
    contact.role = body.role
    contact.email = body.email
    contact.phone = body.phone
    contact.is_primary = body.is_primary
    await db.flush()

    await log_event(
        db,
        action="supplier.contact_updated",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(contact.id),
        metadata={"supplier_id": str(supplier_id), "name": contact.name},
    )
    await db.commit()
    await db.refresh(contact)
    return _contact_to_response(contact)


@router.delete("/{supplier_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier_contact(
    supplier_id: uuid.UUID,
    contact_id: uuid.UUID,
    current: CurrentUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    contact = await get_or_404(
        db,
        SupplierContact,
        current.org_id,
        contact_id,
        "Contact not found",
        SupplierContact.supplier_id == supplier_id,
    )
    await db.delete(contact)
    await log_event(
        db,
        action="supplier.contact_deleted",
        org_id=current.org_id,
        actor_user_id=current.id,
        target=str(contact_id),
        metadata={"supplier_id": str(supplier_id)},
    )
    await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_supplier_lifecycle.py -v`
Expected: all PASS, including the two new tests

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/suppliers.py backend/tests/test_supplier_lifecycle.py
git commit -m "feat: add supplier contacts CRUD endpoints"
```

---

### Task 3: Supplier address/terms/tags + tag filter (backend)

**Files:**
- Modify: `backend/app/api/routes/suppliers.py`
- Test: `backend/tests/test_supplier_lifecycle.py`

**Interfaces:**
- Consumes: `Supplier.address_line1/.../tags` (Task 1).
- Produces: `SupplierResponse` gains `address_line1, address_line2, city, state, postal_code, country, payment_terms_days, lead_time_days, tags`. `GET /suppliers` gains `?tag=`. Task 6 (PO creation defaulting) reads `Supplier.lead_time_days` directly from the model, not through this response - no dependency there. Task 7 (frontend) consumes the extended `SupplierResponse` shape.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_supplier_lifecycle.py`:

```python
async def test_supplier_create_and_update_extended_fields(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/suppliers",
        json={
            "name": "Acme Metals",
            "address_line1": "1 Foundry Rd",
            "city": "Pittsburgh",
            "state": "PA",
            "postal_code": "15201",
            "country": "USA",
            "payment_terms_days": 30,
            "lead_time_days": 14,
            "tags": ["metals", "domestic"],
        },
        headers=headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["city"] == "Pittsburgh"
    assert body["payment_terms_days"] == 30
    assert body["lead_time_days"] == 14
    assert body["tags"] == ["metals", "domestic"]

    updated = await client.patch(
        f"/suppliers/{body['id']}",
        json={"name": "Acme Metals", "lead_time_days": 21, "tags": ["metals"]},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["lead_time_days"] == 21
    assert updated.json()["tags"] == ["metals"]


async def test_list_suppliers_filters_by_tag(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/suppliers", json={"name": "Alpha", "tags": ["metals"]}, headers=headers)
    await client.post("/suppliers", json={"name": "Beta", "tags": ["packaging"]}, headers=headers)
    await client.post(
        "/suppliers", json={"name": "Gamma", "tags": ["metals", "domestic"]}, headers=headers
    )

    res = await client.get("/suppliers?tag=metals", headers=headers)
    assert res.status_code == 200
    assert {s["name"] for s in res.json()} == {"Alpha", "Gamma"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_supplier_lifecycle.py -k "extended_fields or filters_by_tag" -v`
Expected: FAIL (`city`/`tags` keys missing from the response, or 422 on unexpected request fields being silently ignored rather than round-tripped)

- [ ] **Step 3: Implement**

In `backend/app/api/routes/suppliers.py`, add `Field` to the `pydantic` import: `from pydantic import BaseModel, Field`.

Replace the `SupplierCreateRequest`, `SupplierUpdateRequest`, `SupplierResponse`, and `_to_response` definitions with:

```python
class SupplierCreateRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    payment_terms_days: int | None = None
    lead_time_days: int | None = None
    tags: list[str] = Field(default_factory=list)


class SupplierUpdateRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    payment_terms_days: int | None = None
    lead_time_days: int | None = None
    tags: list[str] = Field(default_factory=list)


class SupplierResponse(BaseModel):
    id: str
    name: str
    email: str | None
    phone: str | None
    is_active: bool
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country: str | None
    payment_terms_days: int | None
    lead_time_days: int | None
    tags: list[str]


def _to_response(supplier: Supplier) -> SupplierResponse:
    return SupplierResponse(
        id=str(supplier.id),
        name=supplier.name,
        email=supplier.email,
        phone=supplier.phone,
        is_active=supplier.is_active,
        address_line1=supplier.address_line1,
        address_line2=supplier.address_line2,
        city=supplier.city,
        state=supplier.state,
        postal_code=supplier.postal_code,
        country=supplier.country,
        payment_terms_days=supplier.payment_terms_days,
        lead_time_days=supplier.lead_time_days,
        tags=supplier.tags,
    )
```

Replace the body of `create_supplier` (the `Supplier(...)` construction line) with:

```python
    supplier = Supplier(
        org_id=current.org_id,
        name=body.name,
        email=body.email,
        phone=body.phone,
        address_line1=body.address_line1,
        address_line2=body.address_line2,
        city=body.city,
        state=body.state,
        postal_code=body.postal_code,
        country=body.country,
        payment_terms_days=body.payment_terms_days,
        lead_time_days=body.lead_time_days,
        tags=body.tags,
    )
```

Replace the body of `update_supplier` (the field-assignment lines between `get_or_404` and `try: await db.flush()`) with:

```python
    supplier.name = body.name
    supplier.email = body.email
    supplier.phone = body.phone
    supplier.address_line1 = body.address_line1
    supplier.address_line2 = body.address_line2
    supplier.city = body.city
    supplier.state = body.state
    supplier.postal_code = body.postal_code
    supplier.country = body.country
    supplier.payment_terms_days = body.payment_terms_days
    supplier.lead_time_days = body.lead_time_days
    supplier.tags = body.tags
```

Replace the entire `list_suppliers` function with:

```python
@router.get("", response_model=list[SupplierResponse])
async def list_suppliers(
    q: str | None = Query(None),
    active_only: bool = Query(False),
    tag: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Supplier).where(Supplier.org_id == current.org_id)
    if q:
        query = query.where(Supplier.name.ilike(f"%{q}%"))
    if active_only:
        query = query.where(Supplier.is_active.is_(True))
    query = query.order_by(Supplier.name)

    if tag:
        # Tags are stored as JSON (portable across the Postgres prod DB and
        # the SQLite test DB - see Supplier.tags), so "contains this tag"
        # can't be pushed into one portable SQL WHERE. Org-scoped supplier
        # lists are small (same reasoning the scorecard endpoint uses), so
        # filter and paginate in Python instead for this one case.
        result = await db.execute(query)
        matching = [s for s in result.scalars().all() if tag in s.tags]
        return [_to_response(s) for s in matching[offset : offset + limit]]

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return [_to_response(supplier) for supplier in result.scalars().all()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_supplier_lifecycle.py -v`
Expected: all PASS

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd backend && python -m pytest tests/test_procurement.py tests/test_purchase_order_listing.py tests/test_generate_po.py tests/test_preferred_supplier.py -q`
Expected: all PASS (existing supplier creation calls in these files omit the new fields, which all default to `None`/`[]` - no behavior change)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/suppliers.py backend/tests/test_supplier_lifecycle.py
git commit -m "feat: add supplier address, payment terms, lead time, and tag filtering"
```

---

### Task 4: Supplier scorecard endpoint (backend)

**Files:**
- Modify: `backend/app/api/routes/suppliers.py`
- Test: `backend/tests/test_supplier_lifecycle.py`

**Interfaces:**
- Consumes: `PurchaseOrder`, `PurchaseOrderLine`, `PurchaseOrderReceiptLine` (existing + Task 1's `quantity_rejected`).
- Produces: `GET /suppliers/{supplier_id}/scorecard` -> `{on_time_pct: float | null, defect_pct: float | null, sample_size: int}`. Task 7 (frontend) consumes this exact shape as `SupplierScorecard`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_supplier_lifecycle.py`. First add this import at the top of the file, right after `from __future__ import annotations`:

```python
from datetime import date, timedelta
```

Then append:

```python
async def test_supplier_scorecard_no_data_yet(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)

    res = await client.get(f"/suppliers/{supplier['id']}/scorecard", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"on_time_pct": None, "defect_pct": None, "sample_size": 0}


async def test_supplier_scorecard_on_time_and_defect_math(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)
    item = (
        await client.post(
            "/items",
            json={"sku": "ROD", "name": "Rod", "item_type": "raw_material", "unit_of_measure": "ea"},
            headers=headers,
        )
    ).json()

    async def _po_receive(expected_delivery_date, quantity, quantity_rejected):
        body = {"supplier_id": supplier["id"]}
        if expected_delivery_date is not None:
            body["expected_delivery_date"] = expected_delivery_date
        po = (await client.post("/purchase-orders", json=body, headers=headers)).json()
        lines = await client.patch(
            f"/purchase-orders/{po['id']}",
            json=[
                {"item_id": item["id"], "quantity_ordered": quantity, "unit_price": "1", "sequence": 0}
            ],
            headers=headers,
        )
        line_id = lines.json()["lines"][0]["id"]
        await client.post(f"/purchase-orders/{po['id']}/submit", headers=headers)
        await client.post(
            f"/purchase-orders/{po['id']}/receive",
            json=[{"po_line_id": line_id, "quantity": quantity, "quantity_rejected": quantity_rejected}],
            headers=headers,
        )

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    await _po_receive(tomorrow, "10", "2")  # on time, 2 rejected
    await _po_receive(yesterday, "5", "0")  # late, none rejected
    await _po_receive(None, "5", "0")  # no due date, excluded from on_time_pct

    res = await client.get(f"/suppliers/{supplier['id']}/scorecard", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["sample_size"] == 2
    assert body["on_time_pct"] == pytest.approx(0.5)
    assert body["defect_pct"] == pytest.approx(0.1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_supplier_lifecycle.py -k scorecard -v`
Expected: FAIL with 404 (route doesn't exist yet) for the first test; the second test also fails at the `/receive` call since `quantity_rejected` isn't accepted yet - that's expected, Task 5 adds it. This test won't fully pass until Task 5 lands too; for this step just confirm the scorecard route itself 404s.

- [ ] **Step 3: Implement**

In `backend/app/api/routes/suppliers.py`, add `func` to the `sqlalchemy` import (`from sqlalchemy import select, update` becomes `from sqlalchemy import func, select, update`), and add the PO models to the model import (`from app.models.procurement import Supplier, SupplierContact` becomes `from app.models.procurement import (PurchaseOrder, PurchaseOrderLine, PurchaseOrderReceiptLine, Supplier, SupplierContact)`).

Append to the end of the file:

```python
class SupplierScorecardResponse(BaseModel):
    on_time_pct: float | None
    defect_pct: float | None
    sample_size: int


@router.get("/{supplier_id}/scorecard", response_model=SupplierScorecardResponse)
async def get_supplier_scorecard(
    supplier_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")

    po_rows = (
        await db.execute(
            select(PurchaseOrder.id, PurchaseOrder.expected_delivery_date).where(
                PurchaseOrder.org_id == current.org_id,
                PurchaseOrder.supplier_id == supplier_id,
                PurchaseOrder.status == "received",
                PurchaseOrder.expected_delivery_date.is_not(None),
            )
        )
    ).all()

    on_time_pct: float | None = None
    if po_rows:
        po_ids = [row.id for row in po_rows]
        last_receipt_by_po = dict(
            (
                await db.execute(
                    select(PurchaseOrderLine.po_id, func.max(PurchaseOrderReceiptLine.received_at))
                    .join(
                        PurchaseOrderReceiptLine,
                        PurchaseOrderReceiptLine.po_line_id == PurchaseOrderLine.id,
                    )
                    .where(PurchaseOrderLine.po_id.in_(po_ids))
                    .group_by(PurchaseOrderLine.po_id)
                )
            ).all()
        )
        on_time_count = sum(
            1
            for row in po_rows
            if last_receipt_by_po.get(row.id) is not None
            and last_receipt_by_po[row.id].date() <= row.expected_delivery_date
        )
        on_time_pct = on_time_count / len(po_rows)

    total_received, total_rejected = (
        await db.execute(
            select(
                func.sum(PurchaseOrderReceiptLine.quantity),
                func.sum(PurchaseOrderReceiptLine.quantity_rejected),
            )
            .join(PurchaseOrderLine, PurchaseOrderLine.id == PurchaseOrderReceiptLine.po_line_id)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
            .where(PurchaseOrder.org_id == current.org_id, PurchaseOrder.supplier_id == supplier_id)
        )
    ).first()
    defect_pct = float(total_rejected / total_received) if total_received else None

    return SupplierScorecardResponse(
        on_time_pct=on_time_pct, defect_pct=defect_pct, sample_size=len(po_rows)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_supplier_lifecycle.py -k scorecard -v`
Expected: `test_supplier_scorecard_no_data_yet` PASSES. `test_supplier_scorecard_on_time_and_defect_math` still FAILS at the `quantity_rejected` request field (422 or the field is silently dropped) until Task 5 - confirm the failure is specifically about `quantity_rejected`, not about the scorecard math itself, then proceed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/suppliers.py backend/tests/test_supplier_lifecycle.py
git commit -m "feat: add supplier on-time/defect scorecard endpoint"
```

---

### Task 5: Receive endpoint reject-quantity handling (backend)

**Files:**
- Modify: `backend/app/api/routes/purchase_orders.py`
- Test: `backend/tests/test_procurement.py`

**Interfaces:**
- Consumes: `PurchaseOrderReceiptLine.quantity_rejected/reject_reason` (Task 1).
- Produces: `ReceiveLineInput` gains optional `quantity_rejected: Decimal = Decimal(0)`, `reject_reason: str | None = None`. This is what makes `test_supplier_scorecard_on_time_and_defect_math` (Task 4) fully pass. Task 8 (frontend) sends these two fields from the receive dialog.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_procurement.py`:

```python
async def test_receive_with_rejected_quantity_only_applies_accepted_to_inventory(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "10", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    receive = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {
                "po_line_id": line_id,
                "quantity": "10",
                "quantity_rejected": "3",
                "reject_reason": "damaged",
            }
        ],
        headers=headers,
    )
    assert receive.status_code == 200
    # The full quantity (accepted + rejected) still closes out the line.
    assert receive.json()["lines"][0]["quantity_received"] == "10.0000"
    assert receive.json()["status"] == "received"

    inventory = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert inventory.status_code == 200
    # Only the accepted 7 units landed in inventory, not the full 10.
    assert inventory.json()["quantity_on_hand"] == "7.0000"


async def test_receive_fully_rejected_applies_no_inventory_transaction(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "5", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    receive = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[
            {
                "po_line_id": line_id,
                "quantity": "5",
                "quantity_rejected": "5",
                "reject_reason": "wrong spec",
            }
        ],
        headers=headers,
    )
    assert receive.status_code == 200
    assert receive.json()["status"] == "received"

    inventory = await client.get(f"/inventory/{item['id']}", headers=headers)
    assert inventory.status_code == 200
    assert inventory.json()["quantity_on_hand"] == "0.0000"


async def test_receive_rejects_quantity_rejected_exceeding_quantity(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    item = await _create_item(client, token)
    supplier = await _create_supplier(client, token)

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    po_id = po.json()["id"]
    lines = await client.patch(
        f"/purchase-orders/{po_id}",
        json=[{"item_id": item["id"], "quantity_ordered": "5", "unit_price": "1", "sequence": 0}],
        headers=headers,
    )
    line_id = lines.json()["lines"][0]["id"]
    await client.post(f"/purchase-orders/{po_id}/submit", headers=headers)

    receive = await client.post(
        f"/purchase-orders/{po_id}/receive",
        json=[{"po_line_id": line_id, "quantity": "5", "quantity_rejected": "6"}],
        headers=headers,
    )
    assert receive.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_procurement.py -k rejected -v`
Expected: FAIL - `quantity_rejected` isn't a recognized field yet, so the first two tests see the full quantity land in inventory (`quantity_on_hand` is `"10.0000"`/`"5.0000"`, not the expected accepted-only figures) and the third test gets `200` instead of `400`.

- [ ] **Step 3: Implement**

In `backend/app/api/routes/purchase_orders.py`, replace the `ReceiveLineInput` class with:

```python
class ReceiveLineInput(BaseModel):
    po_line_id: str
    quantity: Decimal
    # Portion of `quantity` that failed inspection - not additional to it.
    # See PurchaseOrderReceiptLine.quantity_rejected for the full contract.
    quantity_rejected: Decimal = Decimal(0)
    reject_reason: str | None = None
    # Required if the line's item is lot-tracked, rejected otherwise - see
    # receive_purchase_order's validation loop.
    lot_number: str | None = None
    expiry_date: date | None = None
    # Which location the stock lands in. Defaults to the org's default location.
    location_id: str | None = None
```

In `receive_purchase_order`, in the validation loop (the `for receipt in receipts_in:` block that raises `HTTPException`s before any writes), add a new check right after the existing over-receipt check (`if line.quantity_received + receipt.quantity > line.quantity_ordered: ...`):

```python
        if receipt.quantity_rejected < 0 or receipt.quantity_rejected > receipt.quantity:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "quantity_rejected must be between 0 and quantity"
            )
```

Replace the write loop (the second `for receipt in receipts_in:` block, from `line.quantity_received += receipt.quantity` through the end of the `else:` branch's `apply_inventory_delta` call) with:

```python
    for receipt in receipts_in:
        line_id = uuid.UUID(receipt.po_line_id)
        line = lines_by_id[line_id]
        item = items_by_line_id[line_id]
        line.quantity_received += receipt.quantity
        accepted_quantity = receipt.quantity - receipt.quantity_rejected

        receipt_line = PurchaseOrderReceiptLine(
            id=uuid.uuid4(),
            org_id=current.org_id,
            po_line_id=line.id,
            quantity=receipt.quantity,
            quantity_rejected=receipt.quantity_rejected,
            reject_reason=receipt.reject_reason,
            received_by=current.id,
        )
        db.add(receipt_line)

        if accepted_quantity == 0:
            # Fully rejected: nothing to apply to inventory/lot stock.
            continue

        # Defaults to the org's default location if unspecified - see
        # services.locations. Shared by both branches below.
        location_id = await resolve_location_id(db, current.org_id, receipt.location_id)

        if item.is_lot_tracked:
            lot_number = receipt.lot_number
            if lot_number is None:
                # Unreachable given the validation loop above already checked
                # this, but a real guard rather than an assert (which
                # vanishes under python -O) that also satisfies the type
                # checker that lot_number is str, not str | None, below.
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "lot_number is required")
            lot = await find_or_create_lot(
                db,
                org_id=current.org_id,
                item_id=item.id,
                lot_number=lot_number,
                location_id=location_id,
                quantity=accepted_quantity,
                expiry_date=receipt.expiry_date,
                created_by=current.id,
            )
            db.add(
                LotTransaction(
                    org_id=current.org_id,
                    lot_id=lot.id,
                    transaction_type="receipt",
                    quantity=accepted_quantity,
                    source_receipt_line_id=receipt_line.id,
                    created_by=current.id,
                )
            )
        else:
            # Non-lot-tracked: the received quantity lands in the general
            # inventory on-hand ledger at the target location, so this item's
            # stock is real for MRP netting instead of untracked - see
            # services.inventory.
            await apply_inventory_delta(
                db,
                org_id=current.org_id,
                item_id=item.id,
                location_id=location_id,
                delta=accepted_quantity,
                transaction_type="receipt",
                created_by=current.id,
                source_receipt_line_id=receipt_line.id,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_procurement.py -k rejected -v`
Expected: all PASS

Run: `cd backend && python -m pytest tests/test_supplier_lifecycle.py -k scorecard -v`
Expected: `test_supplier_scorecard_on_time_and_defect_math` now PASSES too (Task 4's test depended on this)

- [ ] **Step 5: Run the full procurement/concurrency suite to check for regressions**

Run: `cd backend && python -m pytest tests/test_procurement.py tests/test_purchase_order_listing.py tests/test_concurrency.py tests/test_lot_tracking.py -q`
Expected: all PASS (existing `/receive` calls omit `quantity_rejected`, which defaults to `0`, so `accepted_quantity == receipt.quantity` for all of them - identical behavior to before)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/purchase_orders.py backend/tests/test_procurement.py
git commit -m "feat: track rejected quantity on PO receiving, only accepted stock hits inventory"
```

---

### Task 6: Default `expected_delivery_date` from supplier lead time (backend)

**Files:**
- Modify: `backend/app/api/routes/purchase_orders.py`
- Modify: `backend/app/api/routes/forecasts.py`
- Test: `backend/tests/test_procurement.py`, `backend/tests/test_generate_po.py`

**Interfaces:**
- Consumes: `Supplier.lead_time_days` (Task 1).
- Produces: both `create_purchase_order` (direct PO creation) and `_new_draft_po` (used by `/mrp/generate-po` and `/mrp/generate-pos`) default a new draft's `expected_delivery_date` to `today + lead_time_days` when the caller doesn't supply one and the supplier has a lead time set.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_procurement.py`. First add this import at the top of the file, right after `from __future__ import annotations`:

```python
from datetime import date, timedelta
```

Then append:

```python
async def test_create_po_defaults_expected_delivery_date_from_supplier_lead_time(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    supplier = (
        await client.post(
            "/suppliers", json={"name": "Acme Metals", "lead_time_days": 14}, headers=headers
        )
    ).json()

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    assert po.status_code == 201
    expected = (date.today() + timedelta(days=14)).isoformat()
    assert po.json()["expected_delivery_date"] == expected


async def test_create_po_leaves_expected_delivery_date_null_without_lead_time(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = await _create_supplier(client, token)  # no lead_time_days set

    po = await client.post("/purchase-orders", json={"supplier_id": supplier["id"]}, headers=headers)
    assert po.status_code == 201
    assert po.json()["expected_delivery_date"] is None


async def test_create_po_explicit_expected_delivery_date_overrides_lead_time_default(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    supplier = (
        await client.post(
            "/suppliers", json={"name": "Acme Metals", "lead_time_days": 14}, headers=headers
        )
    ).json()

    po = await client.post(
        "/purchase-orders",
        json={"supplier_id": supplier["id"], "expected_delivery_date": "2026-12-25"},
        headers=headers,
    )
    assert po.status_code == 201
    assert po.json()["expected_delivery_date"] == "2026-12-25"
```

Append to `backend/tests/test_generate_po.py`. First add this import at the top of the file, right after `from decimal import Decimal`:

```python
from datetime import date, timedelta
```

Then replace the `_create_supplier` helper with a version that accepts an optional lead time:

```python
async def _create_supplier(client, token, name="Acme Metals", lead_time_days=None):
    res = await client.post(
        "/suppliers",
        json={"name": name, "lead_time_days": lead_time_days},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201
    return res.json()
```

Then append a new test at the end of the file:

```python
async def test_generate_po_defaults_expected_delivery_date_from_supplier_lead_time(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    item = await _create_item(client, token, "ROD", "Steel Rod")
    supplier = await _create_supplier(client, token, lead_time_days=14)
    await _forecast(client, token, item, 100)

    res = await _generate_po(client, token, supplier, [item["id"]])
    assert res.status_code == 200

    po = await client.get(f"/purchase-orders/{res.json()['po_id']}", headers=headers)
    expected = (date.today() + timedelta(days=14)).isoformat()
    assert po.json()["expected_delivery_date"] == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_procurement.py -k "lead_time" tests/test_generate_po.py -k "lead_time" -v`
Expected: FAIL - `expected_delivery_date` is `None` in all three `test_procurement.py` cases (the default doesn't apply yet) and the `test_generate_po.py` case gets `None` too

- [ ] **Step 3: Implement in `create_purchase_order`**

In `backend/app/api/routes/purchase_orders.py`, update the top `datetime` import: `from datetime import date` becomes `from datetime import date, timedelta`.

In `create_purchase_order`, replace the two lines:

```python
    supplier = await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")

    for attempt in range(MAX_PO_NUMBER_ATTEMPTS):
```

with:

```python
    supplier = await get_or_404(db, Supplier, current.org_id, supplier_id, "Supplier not found")

    expected_delivery_date = body.expected_delivery_date
    if expected_delivery_date is None and supplier.lead_time_days is not None:
        expected_delivery_date = date.today() + timedelta(days=supplier.lead_time_days)

    for attempt in range(MAX_PO_NUMBER_ATTEMPTS):
```

Then, in the same function, replace `expected_delivery_date=body.expected_delivery_date,` (inside the `PurchaseOrder(...)` construction) with `expected_delivery_date=expected_delivery_date,`.

- [ ] **Step 4: Implement in `_new_draft_po` (forecasts.py)**

In `backend/app/api/routes/forecasts.py`, update the top `datetime` import: `from datetime import date, datetime` becomes `from datetime import date, datetime, timedelta`.

Replace the entire `_new_draft_po` function with:

```python
async def _new_draft_po(
    db: AsyncSession, org_id: uuid.UUID, supplier: Supplier, created_by: uuid.UUID
) -> PurchaseOrder:
    """Allocate a po_number and create a draft PO, guarded against the max+1
    read-then-insert race. Uses a per-attempt SAVEPOINT (not a plain rollback)
    so it's safe to call repeatedly within one request - generate_pos creates a
    PO per supplier, and a plain rollback on the 2nd would discard the 1st.
    """
    expected_delivery_date = (
        date.today() + timedelta(days=supplier.lead_time_days)
        if supplier.lead_time_days is not None
        else None
    )
    for attempt in range(MAX_PO_NUMBER_ATTEMPTS):
        max_number = await db.scalar(
            select(func.max(PurchaseOrder.po_number)).where(PurchaseOrder.org_id == org_id)
        )
        po = PurchaseOrder(
            org_id=org_id,
            supplier_id=supplier.id,
            po_number=(max_number or 0) + 1,
            status="draft",
            expected_delivery_date=expected_delivery_date,
            created_by=created_by,
        )
        try:
            async with db.begin_nested():
                db.add(po)
                await db.flush()
            return po
        except IntegrityError:
            if attempt == MAX_PO_NUMBER_ATTEMPTS - 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "Could not allocate a PO number, please retry"
                ) from None
    raise AssertionError("unreachable")
```

Update the two call sites. In `generate_po`, replace `po = await _new_draft_po(db, org_id, supplier_id, current.id)` with `po = await _new_draft_po(db, org_id, supplier, current.id)` (the `supplier` object is already loaded earlier in that function).

In `generate_pos`, replace `po = await _new_draft_po(db, org_id, supplier_id, current.id)` with `po = await _new_draft_po(db, org_id, suppliers[supplier_id], current.id)` (the `suppliers` dict mapping id -> `Supplier` is already built earlier in that function).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_procurement.py -k "lead_time" tests/test_generate_po.py -v`
Expected: all PASS

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `cd backend && python -m pytest tests/ -q`
Expected: all PASS (the `pg_client`-based concurrency tests will SKIP in this environment, as before - that's expected, not a failure)

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes/purchase_orders.py backend/app/api/routes/forecasts.py backend/tests/test_procurement.py backend/tests/test_generate_po.py
git commit -m "feat: default a new draft PO's expected delivery date from supplier lead time"
```

---

### Task 7: Frontend - Suppliers.tsx (address/terms/tags, contacts, scorecard)

**Files:**
- Modify: `frontend/src/pages/Suppliers.tsx` (full-file replacement - the page gains enough new state/queries/JSX that a from-scratch rewrite is clearer than a sequence of patches)

**Interfaces:**
- Consumes: `GET/POST/PATCH /suppliers` (extended, Task 3), `GET/POST/PATCH/DELETE /suppliers/{id}/contacts` (Task 2), `GET /suppliers/{id}/scorecard` (Task 4).
- Produces: nothing consumed by other tasks - this is a leaf page.

- [ ] **Step 1: Replace the file**

Replace the entire contents of `frontend/src/pages/Suppliers.tsx` with:

```tsx
import { FormEvent, useState } from "react";
import { Truck } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { Field } from "@/components/Field";
import { EmptyState } from "@/components/EmptyState";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead, TR, TableWrap } from "@/components/ui/table";

interface SupplierResponse {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  is_active: boolean;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country: string | null;
  payment_terms_days: number | null;
  lead_time_days: number | null;
  tags: string[];
}

interface SupplierContactData {
  id: string;
  name: string;
  role: string | null;
  email: string | null;
  phone: string | null;
  is_primary: boolean;
}

interface SupplierScorecard {
  on_time_pct: number | null;
  defect_pct: number | null;
  sample_size: number;
}

const PAGE_SIZE = 20;

export function SuppliersPanel() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [tagFilter, setTagFilter] = useState<string | null>(null);

  const [editing, setEditing] = useState<SupplierResponse | null>(null);
  const [editName, setEditName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editAddressLine1, setEditAddressLine1] = useState("");
  const [editAddressLine2, setEditAddressLine2] = useState("");
  const [editCity, setEditCity] = useState("");
  const [editState, setEditState] = useState("");
  const [editPostalCode, setEditPostalCode] = useState("");
  const [editCountry, setEditCountry] = useState("");
  const [editPaymentTermsDays, setEditPaymentTermsDays] = useState("");
  const [editLeadTimeDays, setEditLeadTimeDays] = useState("");
  const [editTags, setEditTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [editError, setEditError] = useState<string | null>(null);

  const [contactName, setContactName] = useState("");
  const [contactRole, setContactRole] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [contactPhone, setContactPhone] = useState("");
  const [contactError, setContactError] = useState<string | null>(null);

  const suppliers = useQuery<SupplierResponse[]>({
    queryKey: ["suppliers", search, page, tagFilter],
    queryFn: async () => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
      if (search) params.set("q", search);
      if (tagFilter) params.set("tag", tagFilter);
      const res = await apiFetch(`/suppliers?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load suppliers");
      return res.json();
    },
  });

  const availableTags = Array.from(new Set((suppliers.data ?? []).flatMap((s) => s.tags))).sort();

  const contacts = useQuery<SupplierContactData[]>({
    queryKey: ["suppliers", editing?.id, "contacts"],
    enabled: !!editing,
    queryFn: async () => {
      const res = await apiFetch(`/suppliers/${editing!.id}/contacts`);
      if (!res.ok) throw new Error("Failed to load contacts");
      return res.json();
    },
  });

  const scorecard = useQuery<SupplierScorecard>({
    queryKey: ["suppliers", editing?.id, "scorecard"],
    enabled: !!editing,
    queryFn: async () => {
      const res = await apiFetch(`/suppliers/${editing!.id}/scorecard`);
      if (!res.ok) throw new Error("Failed to load scorecard");
      return res.json();
    },
  });

  const createSupplier = useMutation({
    mutationFn: async () => {
      const res = await apiFetch("/suppliers", {
        method: "POST",
        body: JSON.stringify({ name, email: email || null, phone: phone || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to create supplier");
      }
      return res.json();
    },
    onSuccess: () => {
      setName("");
      setEmail("");
      setPhone("");
      queryClient.invalidateQueries({ queryKey: ["suppliers"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const updateSupplier = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error("No supplier selected");
      const res = await apiFetch(`/suppliers/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: editName,
          email: editEmail || null,
          phone: editPhone || null,
          address_line1: editAddressLine1 || null,
          address_line2: editAddressLine2 || null,
          city: editCity || null,
          state: editState || null,
          postal_code: editPostalCode || null,
          country: editCountry || null,
          payment_terms_days: editPaymentTermsDays ? Number(editPaymentTermsDays) : null,
          lead_time_days: editLeadTimeDays ? Number(editLeadTimeDays) : null,
          tags: editTags,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update supplier");
      }
      return res.json();
    },
    onSuccess: () => {
      setEditing(null);
      queryClient.invalidateQueries({ queryKey: ["suppliers"] });
    },
    onError: (e: Error) => setEditError(e.message),
  });

  const setSupplierActive = useMutation({
    mutationFn: async ({ id, active }: { id: string; active: boolean }) => {
      const res = await apiFetch(`/suppliers/${id}/${active ? "reactivate" : "deactivate"}`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to update supplier");
      }
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suppliers"] }),
    onError: (e: Error) => setError(e.message),
  });

  const addContact = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error("No supplier selected");
      const res = await apiFetch(`/suppliers/${editing.id}/contacts`, {
        method: "POST",
        body: JSON.stringify({
          name: contactName,
          role: contactRole || null,
          email: contactEmail || null,
          phone: contactPhone || null,
          is_primary: false,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to add contact");
      }
      return res.json();
    },
    onSuccess: () => {
      setContactName("");
      setContactRole("");
      setContactEmail("");
      setContactPhone("");
      queryClient.invalidateQueries({ queryKey: ["suppliers", editing?.id, "contacts"] });
    },
    onError: (e: Error) => setContactError(e.message),
  });

  const setContactPrimary = useMutation({
    mutationFn: async (contact: SupplierContactData) => {
      if (!editing) throw new Error("No supplier selected");
      const res = await apiFetch(`/suppliers/${editing.id}/contacts/${contact.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: contact.name,
          role: contact.role,
          email: contact.email,
          phone: contact.phone,
          is_primary: true,
        }),
      });
      if (!res.ok) throw new Error("Failed to update contact");
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suppliers", editing?.id, "contacts"] }),
    onError: (e: Error) => setContactError(e.message),
  });

  const deleteContact = useMutation({
    mutationFn: async (contactId: string) => {
      if (!editing) throw new Error("No supplier selected");
      const res = await apiFetch(`/suppliers/${editing.id}/contacts/${contactId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete contact");
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suppliers", editing?.id, "contacts"] }),
    onError: (e: Error) => setContactError(e.message),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    createSupplier.mutate();
  }

  function openEdit(supplier: SupplierResponse) {
    setEditError(null);
    setContactError(null);
    setEditing(supplier);
    setEditName(supplier.name);
    setEditEmail(supplier.email ?? "");
    setEditPhone(supplier.phone ?? "");
    setEditAddressLine1(supplier.address_line1 ?? "");
    setEditAddressLine2(supplier.address_line2 ?? "");
    setEditCity(supplier.city ?? "");
    setEditState(supplier.state ?? "");
    setEditPostalCode(supplier.postal_code ?? "");
    setEditCountry(supplier.country ?? "");
    setEditPaymentTermsDays(
      supplier.payment_terms_days != null ? String(supplier.payment_terms_days) : "",
    );
    setEditLeadTimeDays(supplier.lead_time_days != null ? String(supplier.lead_time_days) : "");
    setEditTags(supplier.tags);
    setTagInput("");
  }

  function onEditSubmit(e: FormEvent) {
    e.preventDefault();
    setEditError(null);
    updateSupplier.mutate();
  }

  function addTag() {
    const t = tagInput.trim();
    if (t && !editTags.includes(t)) {
      setEditTags([...editTags, t]);
    }
    setTagInput("");
  }

  function removeTag(tag: string) {
    setEditTags(editTags.filter((t) => t !== tag));
  }

  return (
    <div>
      {error && (
        <Alert variant="destructive" className="mb-6">
          {error}
        </Alert>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Add a supplier</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Name" htmlFor="name">
              <Input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <Field label="Email" htmlFor="email">
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </Field>
            <Field label="Phone" htmlFor="phone">
              <Input id="phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
            </Field>
            <div className="flex items-end">
              <Button type="submit" className="w-full" loading={createSupplier.isPending}>
                Add supplier
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <div className="mb-4 max-w-xs">
        <Input
          placeholder="Search suppliers…"
          value={search}
          onChange={(e) => {
            setPage(0);
            setSearch(e.target.value);
          }}
        />
      </div>

      {availableTags.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {availableTags.map((t) => (
            <Badge
              key={t}
              variant={tagFilter === t ? "primary" : "neutral"}
              className="cursor-pointer"
              onClick={() => {
                setPage(0);
                setTagFilter(tagFilter === t ? null : t);
              }}
            >
              {t}
            </Badge>
          ))}
        </div>
      )}

      {suppliers.data && suppliers.data.length === 0 ? (
        <EmptyState
          icon={<Truck />}
          title="No suppliers yet"
          description="Add a vendor to start creating purchase orders."
        />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <TR>
                <TH>Name</TH>
                <TH>Email</TH>
                <TH>Phone</TH>
                <TH>Status</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {suppliers.data?.map((supplier) => (
                <TR key={supplier.id}>
                  <TD className="font-medium">{supplier.name}</TD>
                  <TD className="text-muted-foreground">{supplier.email ?? "—"}</TD>
                  <TD className="text-muted-foreground">{supplier.phone ?? "—"}</TD>
                  <TD>
                    <Badge variant={supplier.is_active ? "success" : "neutral"}>
                      {supplier.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="outline" size="sm" onClick={() => openEdit(supplier)}>
                        Edit
                      </Button>
                      <Button
                        variant={supplier.is_active ? "secondary" : "outline"}
                        size="sm"
                        loading={
                          setSupplierActive.isPending && setSupplierActive.variables?.id === supplier.id
                        }
                        onClick={() =>
                          setSupplierActive.mutate({ id: supplier.id, active: !supplier.is_active })
                        }
                      >
                        {supplier.is_active ? "Deactivate" : "Reactivate"}
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </TableWrap>
      )}

      <div className="mb-8 mt-4 flex items-center justify-between">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={page === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          Previous
        </Button>
        <span className="text-sm text-muted-foreground">Page {page + 1}</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!suppliers.data || suppliers.data.length < PAGE_SIZE}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </Button>
      </div>

      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="Edit supplier"
        className="max-w-2xl"
      >
        <form onSubmit={onEditSubmit} className="space-y-4">
          {editError && <Alert variant="destructive">{editError}</Alert>}
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Name" htmlFor="edit-name">
              <Input
                id="edit-name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                required
              />
            </Field>
            <Field label="Email" htmlFor="edit-email">
              <Input
                id="edit-email"
                type="email"
                value={editEmail}
                onChange={(e) => setEditEmail(e.target.value)}
              />
            </Field>
            <Field label="Phone" htmlFor="edit-phone">
              <Input id="edit-phone" value={editPhone} onChange={(e) => setEditPhone(e.target.value)} />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Address line 1" htmlFor="edit-addr1">
              <Input
                id="edit-addr1"
                value={editAddressLine1}
                onChange={(e) => setEditAddressLine1(e.target.value)}
              />
            </Field>
            <Field label="Address line 2" htmlFor="edit-addr2">
              <Input
                id="edit-addr2"
                value={editAddressLine2}
                onChange={(e) => setEditAddressLine2(e.target.value)}
              />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-4">
            <Field label="City" htmlFor="edit-city">
              <Input id="edit-city" value={editCity} onChange={(e) => setEditCity(e.target.value)} />
            </Field>
            <Field label="State" htmlFor="edit-state">
              <Input id="edit-state" value={editState} onChange={(e) => setEditState(e.target.value)} />
            </Field>
            <Field label="Postal code" htmlFor="edit-postal">
              <Input
                id="edit-postal"
                value={editPostalCode}
                onChange={(e) => setEditPostalCode(e.target.value)}
              />
            </Field>
            <Field label="Country" htmlFor="edit-country">
              <Input
                id="edit-country"
                value={editCountry}
                onChange={(e) => setEditCountry(e.target.value)}
              />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Payment terms (days)" htmlFor="edit-terms">
              <Input
                id="edit-terms"
                type="number"
                min="0"
                value={editPaymentTermsDays}
                onChange={(e) => setEditPaymentTermsDays(e.target.value)}
              />
            </Field>
            <Field label="Lead time (days)" htmlFor="edit-lead-time">
              <Input
                id="edit-lead-time"
                type="number"
                min="0"
                value={editLeadTimeDays}
                onChange={(e) => setEditLeadTimeDays(e.target.value)}
              />
            </Field>
          </div>
          <Field label="Tags" htmlFor="edit-tag-input">
            <div className="flex gap-2">
              <Input
                id="edit-tag-input"
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addTag();
                  }
                }}
                placeholder="Add a tag and press Enter"
              />
              <Button type="button" variant="outline" onClick={addTag}>
                Add
              </Button>
            </div>
            {editTags.length > 0 && (
              <div className="flex flex-wrap gap-2 pt-2">
                {editTags.map((t) => (
                  <Badge
                    key={t}
                    variant="neutral"
                    className="cursor-pointer"
                    onClick={() => removeTag(t)}
                  >
                    {t} ×
                  </Badge>
                ))}
              </div>
            )}
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={updateSupplier.isPending}>
              Save
            </Button>
          </div>
        </form>

        {editing && (
          <div className="mt-6 space-y-3 border-t border-border pt-4">
            <h3 className="text-sm font-semibold">Contacts</h3>
            {contactError && <Alert variant="destructive">{contactError}</Alert>}
            {contacts.data && contacts.data.length > 0 && (
              <ul className="space-y-2">
                {contacts.data.map((c) => (
                  <li key={c.id} className="flex items-center justify-between gap-2 text-sm">
                    <div>
                      <span className="font-medium">{c.name}</span>
                      {c.role && <span className="text-muted-foreground"> · {c.role}</span>}
                      {c.email && <span className="text-muted-foreground"> · {c.email}</span>}
                      {c.phone && <span className="text-muted-foreground"> · {c.phone}</span>}
                      {c.is_primary && (
                        <Badge variant="primary" className="ml-2">
                          Primary
                        </Badge>
                      )}
                    </div>
                    <div className="flex gap-2">
                      {!c.is_primary && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          loading={
                            setContactPrimary.isPending && setContactPrimary.variables?.id === c.id
                          }
                          onClick={() => setContactPrimary.mutate(c)}
                        >
                          Make primary
                        </Button>
                      )}
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        loading={deleteContact.isPending && deleteContact.variables === c.id}
                        onClick={() => deleteContact.mutate(c.id)}
                      >
                        Remove
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_1fr_auto] sm:items-end">
              <Field label="Name" htmlFor="contact-name">
                <Input
                  id="contact-name"
                  value={contactName}
                  onChange={(e) => setContactName(e.target.value)}
                />
              </Field>
              <Field label="Role" htmlFor="contact-role">
                <Input
                  id="contact-role"
                  value={contactRole}
                  onChange={(e) => setContactRole(e.target.value)}
                />
              </Field>
              <Field label="Email" htmlFor="contact-email">
                <Input
                  id="contact-email"
                  type="email"
                  value={contactEmail}
                  onChange={(e) => setContactEmail(e.target.value)}
                />
              </Field>
              <Field label="Phone" htmlFor="contact-phone">
                <Input
                  id="contact-phone"
                  value={contactPhone}
                  onChange={(e) => setContactPhone(e.target.value)}
                />
              </Field>
              <Button
                type="button"
                variant="outline"
                loading={addContact.isPending}
                disabled={!contactName.trim()}
                onClick={() => addContact.mutate()}
              >
                Add contact
              </Button>
            </div>
          </div>
        )}

        {editing && scorecard.data && (
          <div className="mt-6 space-y-2 border-t border-border pt-4">
            <h3 className="text-sm font-semibold">Performance</h3>
            {scorecard.data.sample_size === 0 ? (
              <p className="text-sm text-muted-foreground">Not enough data yet.</p>
            ) : (
              <div className="flex gap-4 text-sm">
                <span>
                  On-time delivery:{" "}
                  <strong>
                    {scorecard.data.on_time_pct != null
                      ? `${Math.round(scorecard.data.on_time_pct * 100)}%`
                      : "—"}
                  </strong>
                </span>
                <span>
                  Defect rate:{" "}
                  <strong>
                    {scorecard.data.defect_pct != null
                      ? `${Math.round(scorecard.data.defect_pct * 100)}%`
                      : "—"}
                  </strong>
                </span>
              </div>
            )}
          </div>
        )}
      </Dialog>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no output, exit code 0

- [ ] **Step 3: Lint**

Run: `cd frontend && npm run lint`
Expected: no errors

- [ ] **Step 4: Manual smoke check**

Start the dev server (`npm run dev` in `frontend/`), open the Procurement page's Suppliers tab, add a supplier, click Edit, fill in an address/payment terms/lead time/tags, add a contact and mark it primary, save, and confirm the row and reopened dialog reflect the changes. Confirm the "Performance" section shows "Not enough data yet." for a brand-new supplier with no purchase history.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Suppliers.tsx
git commit -m "feat: add supplier contacts, address/terms/tags, and scorecard to the Suppliers page"
```

---

### Task 8: Frontend - PurchaseOrders.tsx receive dialog reject-quantity/reason inputs

**Files:**
- Modify: `frontend/src/pages/PurchaseOrders.tsx`

**Interfaces:**
- Consumes: `POST /purchase-orders/{po_id}/receive` gains optional `quantity_rejected`/`reject_reason` (Task 5).
- Produces: nothing consumed by other tasks - this is a leaf page.

- [ ] **Step 1: Add state for the two new fields**

In `frontend/src/pages/PurchaseOrders.tsx`, find this block near the top of `PurchaseOrdersPanel`:

```tsx
  const [receiveLineId, setReceiveLineId] = useState("");
  const [receiveQuantity, setReceiveQuantity] = useState("1");
  const [receiveLotNumber, setReceiveLotNumber] = useState("");
  const [receiveExpiryDate, setReceiveExpiryDate] = useState("");
```

Replace it with:

```tsx
  const [receiveLineId, setReceiveLineId] = useState("");
  const [receiveQuantity, setReceiveQuantity] = useState("1");
  const [receiveQuantityRejected, setReceiveQuantityRejected] = useState("0");
  const [receiveRejectReason, setReceiveRejectReason] = useState("");
  const [receiveLotNumber, setReceiveLotNumber] = useState("");
  const [receiveExpiryDate, setReceiveExpiryDate] = useState("");
```

- [ ] **Step 2: Send the new fields in the receive mutation and reset them on success**

Find the `receiveLine` mutation:

```tsx
  const receiveLine = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/purchase-orders/${selectedPoId}/receive`, {
        method: "POST",
        body: JSON.stringify([
          {
            po_line_id: receiveLineId,
            quantity: receiveQuantity,
            lot_number: receivingLineIsLotTracked ? receiveLotNumber : null,
            expiry_date: receivingLineIsLotTracked && receiveExpiryDate ? receiveExpiryDate : null,
          },
        ]),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to receive");
      }
      return res.json();
    },
    onSuccess: () => {
      setReceiveLineId("");
      setReceiveQuantity("1");
      setReceiveLotNumber("");
      setReceiveExpiryDate("");
      invalidatePo();
      queryClient.invalidateQueries({ queryKey: ["lots"] });
    },
    onError: (e: Error) => setError(e.message),
  });
```

Replace it with:

```tsx
  const receiveLine = useMutation({
    mutationFn: async () => {
      const res = await apiFetch(`/purchase-orders/${selectedPoId}/receive`, {
        method: "POST",
        body: JSON.stringify([
          {
            po_line_id: receiveLineId,
            quantity: receiveQuantity,
            quantity_rejected: receiveQuantityRejected || "0",
            reject_reason: receiveRejectReason || null,
            lot_number: receivingLineIsLotTracked ? receiveLotNumber : null,
            expiry_date: receivingLineIsLotTracked && receiveExpiryDate ? receiveExpiryDate : null,
          },
        ]),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? "Failed to receive");
      }
      return res.json();
    },
    onSuccess: () => {
      setReceiveLineId("");
      setReceiveQuantity("1");
      setReceiveQuantityRejected("0");
      setReceiveRejectReason("");
      setReceiveLotNumber("");
      setReceiveExpiryDate("");
      invalidatePo();
      queryClient.invalidateQueries({ queryKey: ["lots"] });
    },
    onError: (e: Error) => setError(e.message),
  });
```

- [ ] **Step 3: Add the two inputs to the receive form**

Find this block inside the receive form's `<div className="grid gap-4 sm:grid-cols-2">`:

```tsx
                  <Field label="Quantity received" htmlFor="rcv-qty">
                    <Input
                      id="rcv-qty"
                      type="number"
                      step="0.0001"
                      min="0.0001"
                      value={receiveQuantity}
                      onChange={(e) => setReceiveQuantity(e.target.value)}
                      required
                    />
                  </Field>
                  {receivingLineIsLotTracked && (
```

Replace it with:

```tsx
                  <Field label="Quantity received" htmlFor="rcv-qty">
                    <Input
                      id="rcv-qty"
                      type="number"
                      step="0.0001"
                      min="0.0001"
                      value={receiveQuantity}
                      onChange={(e) => setReceiveQuantity(e.target.value)}
                      required
                    />
                  </Field>
                  <Field label="Rejected quantity" htmlFor="rcv-qty-rejected">
                    <Input
                      id="rcv-qty-rejected"
                      type="number"
                      step="0.0001"
                      min="0"
                      max={receiveQuantity || undefined}
                      value={receiveQuantityRejected}
                      onChange={(e) => setReceiveQuantityRejected(e.target.value)}
                    />
                  </Field>
                  <Field label="Reject reason (optional)" htmlFor="rcv-reject-reason">
                    <Input
                      id="rcv-reject-reason"
                      value={receiveRejectReason}
                      onChange={(e) => setReceiveRejectReason(e.target.value)}
                    />
                  </Field>
                  {receivingLineIsLotTracked && (
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no output, exit code 0

- [ ] **Step 5: Lint**

Run: `cd frontend && npm run lint`
Expected: no errors

- [ ] **Step 6: Manual smoke check**

Start the dev server, open a submitted PO in the Purchase Orders tab, receive a line with a non-zero rejected quantity and a reason, confirm the request succeeds, and confirm (via the Suppliers page's scorecard, from Task 7) that the defect rate reflects it.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/PurchaseOrders.tsx
git commit -m "feat: capture rejected quantity and reason when receiving a PO line"
```
