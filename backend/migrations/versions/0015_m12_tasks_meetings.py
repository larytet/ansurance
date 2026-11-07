"""m12 tasks and meetings

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

GRANTS = {"agent": ["task.write"], "backoffice": ["task.write"], "manager": ["task.write"]}


def upgrade() -> None:
    op.execute("INSERT INTO capability (name) VALUES ('task.write')")
    for role, caps in GRANTS.items():
        for c in caps:
            op.execute(f"INSERT INTO role_capability (role, capability) VALUES ('{role}', '{c}')")
    op.create_table('task',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('text', sa.String(length=200), nullable=False),
    sa.Column('due_on', sa.Date(), nullable=False),
    sa.Column('client_id', sa.UUID(), nullable=True),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('delegated_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['user_account.id'], ),
    sa.ForeignKeyConstraint(['delegated_by'], ['user_account.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['user_account.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_task_client_id'), 'task', ['client_id'], unique=False)
    op.create_index('ix_task_owner_open', 'task', ['owner_id', 'done_at', 'due_on'], unique=False)
    op.create_table('meeting',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('client_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('held_on', sa.Date(), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['client.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['user_account.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_meeting_client_id'), 'meeting', ['client_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_meeting_client_id'), table_name='meeting')
    op.drop_table('meeting')
    op.drop_index('ix_task_owner_open', table_name='task')
    op.drop_index(op.f('ix_task_client_id'), table_name='task')
    op.drop_table('task')
    op.execute("DELETE FROM role_capability WHERE capability = 'task.write'")
    op.execute("DELETE FROM capability WHERE name = 'task.write'")
