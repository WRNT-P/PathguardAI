"""A deactivated danger zone must stay deactivated across a re-seed.

`DELETE /api/danger-zones/{id}` is a soft delete: a human looked at a zone and
decided the place is not a hazard. The seed's existence check used to ask only
about *active* rows, so that decision was invisible to it and the next run put
the zone back, active, as a new row -- undoing a safety decision with a command
people run routinely and see nothing wrong with.

That is not hypothetical: the two Bangkok demo zones were deactivated against
the live database on 2026-09-06, and every future `python -m
app.mock.seed_risk_rules` would have re-armed both.
"""
import pytest
from sqlalchemy import select

from app.db.models import DangerZone
from app.mock.seed_risk_rules import SEED_DANGER_ZONES, seed_rules


@pytest.mark.asyncio
async def test_reseeding_does_not_revive_a_deactivated_zone(session_factory):
    async with session_factory() as session:
        # conftest already seeded; deactivate one the way the endpoint does.
        zone = (await session.execute(
            select(DangerZone).where(DangerZone.name == SEED_DANGER_ZONES[0]["name"])
        )).scalars().first()
        assert zone is not None, "conftest should have seeded the demo zones"
        zone.active = False
        await session.commit()

        counts = await seed_rules(session)
        await session.commit()

        assert counts["danger_zones"] == 0, "re-seeding revived a deactivated zone"

        rows = (await session.execute(
            select(DangerZone).where(DangerZone.name == SEED_DANGER_ZONES[0]["name"])
        )).scalars().all()
        # One row, still off. A second row would be the old bug: the zone comes
        # back under the same name with a new id, so it does not even look like
        # the one that was switched off.
        assert len(rows) == 1
        assert rows[0].active is False


@pytest.mark.asyncio
async def test_seed_still_fills_a_zone_that_was_never_there(session_factory):
    """The repair path stays: a name absent entirely is still inserted."""
    async with session_factory() as session:
        zone = (await session.execute(
            select(DangerZone).where(DangerZone.name == SEED_DANGER_ZONES[0]["name"])
        )).scalars().first()
        await session.delete(zone)
        await session.commit()

        counts = await seed_rules(session)
        await session.commit()

        assert counts["danger_zones"] == 1
        revived = (await session.execute(
            select(DangerZone).where(DangerZone.name == SEED_DANGER_ZONES[0]["name"])
        )).scalars().first()
        assert revived is not None and revived.active is True
