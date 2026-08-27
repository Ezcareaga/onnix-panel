"""Add pgvector extension and description_embedding column.

Revision ID: 015
Revises: 014
Create Date: 2026-03-26
"""
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add pgvector extension, embedding column, and HNSW index."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "ALTER TABLE properties "
        "ADD COLUMN IF NOT EXISTS description_embedding vector(768)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_properties_embedding_hnsw "
        "ON properties USING hnsw (description_embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    """Remove embedding index and column (keep extension)."""
    op.execute("DROP INDEX IF EXISTS idx_properties_embedding_hnsw")
    op.execute(
        "ALTER TABLE properties "
        "DROP COLUMN IF EXISTS description_embedding"
    )
