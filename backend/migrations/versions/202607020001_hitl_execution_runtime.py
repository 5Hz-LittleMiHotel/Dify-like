from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202607020001"
down_revision = "202606140002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("phase", sa.String(length=32), server_default="queued", nullable=False))
    op.add_column("runs", sa.Column("current_node_id", sa.String(length=120), server_default="", nullable=False))
    op.add_column(
        "runs",
        sa.Column("checkpoint_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column("runs", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.add_column("runs", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("runs", "status", server_default="queued")
    op.create_index(op.f("ix_runs_status"), "runs", ["status"], unique=False)

    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_run_events_run_id"), "run_events", ["run_id"], unique=False)

    op.create_table(
        "run_commands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("command_type", sa.String(length=32), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_run_commands_run_id"), "run_commands", ["run_id"], unique=False)
    op.create_index(op.f("ix_run_commands_status"), "run_commands", ["status"], unique=False)

    op.create_table(
        "human_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("node_id", sa.String(length=120), nullable=False),
        sa.Column("input_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("default_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("responded_by_user_id", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_human_tasks_run_id"), "human_tasks", ["run_id"], unique=False)
    op.create_index(op.f("ix_human_tasks_status"), "human_tasks", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_human_tasks_status"), table_name="human_tasks")
    op.drop_index(op.f("ix_human_tasks_run_id"), table_name="human_tasks")
    op.drop_table("human_tasks")
    op.drop_index(op.f("ix_run_commands_status"), table_name="run_commands")
    op.drop_index(op.f("ix_run_commands_run_id"), table_name="run_commands")
    op.drop_table("run_commands")
    op.drop_index(op.f("ix_run_events_run_id"), table_name="run_events")
    op.drop_table("run_events")
    op.drop_index(op.f("ix_runs_status"), table_name="runs")
    op.drop_column("runs", "ended_at")
    op.drop_column("runs", "updated_at")
    op.drop_column("runs", "checkpoint_json")
    op.drop_column("runs", "current_node_id")
    op.drop_column("runs", "phase")
