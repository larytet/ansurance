"""m19 sandbox: derived page images, and the capability to download an original

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

GRANTS = {"backoffice": ["document.download_original"], "manager": ["document.download_original"]}


def upgrade() -> None:
    op.execute("INSERT INTO capability (name) VALUES ('document.download_original')")
    for role, caps in GRANTS.items():
        for c in caps:
            op.execute(f"INSERT INTO role_capability (role, capability) VALUES ('{role}', '{c}')")
    op.create_table('blob_render',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('blob_id', sa.UUID(), nullable=False),
    sa.Column('tool', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('pages', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=60), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['blob_id'], ['blob.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('blob_id', 'tool', name='uq_blob_render')
    )
    op.create_index(op.f('ix_blob_render_blob_id'), 'blob_render', ['blob_id'], unique=False)
    op.create_table('blob_page',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('render_id', sa.UUID(), nullable=False),
    sa.Column('page_no', sa.Integer(), nullable=False),
    sa.Column('image_blob_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['image_blob_id'], ['blob.id'], ),
    sa.ForeignKeyConstraint(['render_id'], ['blob_render.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('render_id', 'page_no', name='uq_blob_page')
    )
    op.create_index(op.f('ix_blob_page_render_id'), 'blob_page', ['render_id'], unique=False)


def downgrade() -> None:
    op.drop_table('blob_page')
    op.drop_table('blob_render')
    op.execute("DELETE FROM role_capability WHERE capability = 'document.download_original'")
    op.execute("DELETE FROM capability WHERE name = 'document.download_original'")
