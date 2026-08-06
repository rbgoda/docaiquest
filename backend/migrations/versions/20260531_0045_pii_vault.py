"""M44.P11.2 · PII-at-rest vault + per-document reveal flags.

Reversible PII protection in our OWN storage. When `pii_protect_at_rest` is on,
the ingestion pipeline tokenizes PII inside `document_chunks.text` and
`documents.extracted_fields` (so what we persist shows `[CREDIT_CARD_1]` etc.)
and stashes the real values, ENCRYPTED, in `pii_vault` keyed by (document, token).

An owner/admin/reviewer can REVEAL a specific document — the read paths
detokenize from the vault on the fly. `documents.pii_protected` records that a
doc's stored text is tokenized; `pii_revealed` is the per-doc toggle.

All additive + nullable/defaulted → zero behaviour change for existing rows.
The vault CASCADE-deletes with its document, so deleting a doc purges its PII.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045_pii_vault"
down_revision: Union[str, Sequence[str], None] = "0044_chunk_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pii_vault",
        sa.Column("pk", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, index=True),
        sa.Column(
            "document_pk",
            sa.Integer(),
            sa.ForeignKey("documents.pk", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token", sa.String(length=64), nullable=False),   # e.g. [CREDIT_CARD_1]
        sa.Column("kind", sa.String(length=32), nullable=False),    # credit_card / passport / ...
        sa.Column("value_encrypted", sa.Text(), nullable=False),    # Fernet ciphertext (urlsafe b64)
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    # One row per (doc, token); fast detokenize lookup by doc.
    op.create_index(
        "ix_pii_vault_doc_token", "pii_vault", ["document_pk", "token"], unique=True
    )

    op.add_column(
        "documents",
        sa.Column("pii_protected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "documents",
        sa.Column("pii_revealed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("documents", "pii_revealed")
    op.drop_column("documents", "pii_protected")
    op.drop_index("ix_pii_vault_doc_token", table_name="pii_vault")
    op.drop_table("pii_vault")
