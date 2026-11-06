"""m8 capabilities: import, excel join, export

Revision ID: 0010
Revises: 0009
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

GRANTS = {
    "backoffice": ["import.run", "excel.use", "export.run"],
    "marketing": ["excel.use"],
    "manager": ["import.run", "excel.use", "export.run"],
}


def upgrade() -> None:
    for c in ("import.run", "excel.use", "export.run"):
        op.execute(f"INSERT INTO capability (name) VALUES ('{c}')")
    for role, caps in GRANTS.items():
        for c in caps:
            op.execute(f"INSERT INTO role_capability (role, capability) VALUES ('{role}', '{c}')")


def downgrade() -> None:
    op.execute("DELETE FROM role_capability WHERE capability IN ('import.run','excel.use','export.run')")
    op.execute("DELETE FROM capability WHERE name IN ('import.run','excel.use','export.run')")
