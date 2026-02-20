"""add unified watchlist table

Revision ID: c1f2a3b4d5e6
Revises: 45d84d35b8d7
Create Date: 2026-02-20 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1f2a3b4d5e6'
down_revision: Union[str, Sequence[str], None] = '45d84d35b8d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create watchlist table and migrate data from breakout_watchlist."""
    import json
    from datetime import datetime

    op.create_table('watchlist',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('ticker', sa.String(length=10), nullable=False),
        sa.Column('setup_type', sa.Enum('breakout', 'episodic_pivot', 'parabolic_short', name='watchlist_setup_type_enum'), nullable=False),
        sa.Column('stage', sa.Enum('watching', 'ready', 'active', 'triggered', 'expired', 'failed', name='watchlist_unified_stage_enum'), nullable=False),
        sa.Column('scan_date', sa.Date(), nullable=False),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('added_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('stage_changed_at', sa.DateTime(), nullable=False),
        sa.Column('notes', sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('watchlist', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_watchlist_ticker'), ['ticker'], unique=False)
        batch_op.create_index(batch_op.f('ix_watchlist_scan_date'), ['scan_date'], unique=False)

    # Migrate active data from breakout_watchlist -> watchlist
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, ticker, stage, consolidation_days, atr_ratio, "
                "higher_lows, near_10d_ma, near_20d_ma, volume_drying, "
                "rs_composite, added_at, updated_at, stage_changed_at, notes "
                "FROM breakout_watchlist "
                "WHERE stage IN ('watching', 'ready')")
    ).fetchall()

    now = datetime.utcnow()
    for row in rows:
        meta = {
            "consolidation_days": row[3],
            "atr_ratio": row[4],
            "higher_lows": bool(row[5]),
            "near_10d_ma": bool(row[6]),
            "near_20d_ma": bool(row[7]),
            "volume_drying": bool(row[8]),
            "rs_composite": row[9],
            "qualifies": row[2] == "ready",
            "has_prior_move": True,
            "atr_contracting": (row[4] or 1.0) < 0.85,
        }
        # Use added_at date as scan_date
        added_at = row[10]
        if isinstance(added_at, str):
            scan_date = added_at[:10]
        else:
            scan_date = added_at.date().isoformat() if added_at else now.date().isoformat()

        conn.execute(
            sa.text(
                "INSERT INTO watchlist "
                "(ticker, setup_type, stage, scan_date, metadata_json, "
                "added_at, updated_at, stage_changed_at, notes) "
                "VALUES (:ticker, :setup_type, :stage, :scan_date, :meta, "
                ":added_at, :updated_at, :stage_changed_at, :notes)"
            ),
            {
                "ticker": row[1],
                "setup_type": "breakout",
                "stage": row[2],  # watching or ready
                "scan_date": scan_date,
                "meta": json.dumps(meta),
                "added_at": row[10] or now,
                "updated_at": row[11] or now,
                "stage_changed_at": row[12] or now,
                "notes": row[13],
            },
        )


def downgrade() -> None:
    """Drop watchlist table."""
    with op.batch_alter_table('watchlist', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_watchlist_scan_date'))
        batch_op.drop_index(batch_op.f('ix_watchlist_ticker'))

    op.drop_table('watchlist')
