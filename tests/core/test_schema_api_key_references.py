"""Every FK into ``api_keys`` says what deleting a key does to it (#543).

A key is user-deletable from the admin settings page, and that route issues a bare
``DELETE FROM api_keys``. Any referencing FK left at the default NO ACTION turns
that delete into an unhandled ForeignKeyViolationError — a 500 — for every key the
table has ever pointed at. #543 was the voice-embedding table's
``created_by_key_id``; a future per-model ``person_embeddings_*`` table cloned from
it would repeat the mistake.

The two legitimate answers: ``CASCADE`` for rows the key *owns* (its scopes, its
entity subscriptions) and ``SET NULL`` for rows it merely *sourced* (the #311
provenance convention). Swept over the live catalog after ``apply_schema``, so it
sees both an inline definition on a fresh DB and a reconciliation block's repair
on an existing one.
"""

import pytest

pytestmark = [
    pytest.mark.integration,
]

_API_KEY_FKS_SQL = """
SELECT conrelid::regclass::text AS tbl, conname, confdeltype::text AS on_delete
FROM pg_constraint
WHERE contype = 'f' AND confrelid = 'api_keys'::regclass
ORDER BY 1, 2
"""

# 'c' = CASCADE, 'n' = SET NULL.
_ALLOWED_ON_DELETE = {"c", "n"}


async def test_every_api_keys_reference_declares_cascade_or_set_null(db_pool):
    async with db_pool.acquire() as conn:
        fks = await conn.fetch(_API_KEY_FKS_SQL)

    assert fks, "no FKs reference api_keys — the sweep would pass vacuously"
    offenders = [
        f"{fk['tbl']}.{fk['conname']} (confdeltype={fk['on_delete']!r})"
        for fk in fks
        if fk["on_delete"] not in _ALLOWED_ON_DELETE
    ]
    assert offenders == []
