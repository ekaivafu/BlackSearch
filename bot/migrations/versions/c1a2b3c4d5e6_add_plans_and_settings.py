"""add plans and bot_settings

Revision ID: c1a2b3c4d5e6
Revises: b8c6138a016d
Create Date: 2026-09-03 21:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c1a2b3c4d5e6'
down_revision: Union[str, None] = 'b8c6138a016d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # plans table
    op.create_table(
        'plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('plan_type', sa.Enum('CREDITS', 'DAYS', name='plantype'), nullable=False),
        sa.Column('credits', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('price', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_plans_id'), 'plans', ['id'], unique=False)

    # bot_settings table
    op.create_table(
        'bot_settings',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.String(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('key')
    )
    op.create_index(op.f('ix_bot_settings_key'), 'bot_settings', ['key'], unique=False)

    # plan_id in recharge_requests
    op.add_column('recharge_requests', sa.Column('plan_id', sa.Integer(), sa.ForeignKey('plans.id', ondelete='SET NULL'), nullable=True))

def downgrade() -> None:
    op.drop_column('recharge_requests', 'plan_id')
    op.drop_index(op.f('ix_bot_settings_key'), table_name='bot_settings')
    op.drop_table('bot_settings')
    op.drop_index(op.f('ix_plans_id'), table_name='plans')
    op.drop_table('plans')
