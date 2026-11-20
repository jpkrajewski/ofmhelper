from functools import lru_cache

from ofmhelpers.db.repositories.distribution import DistributionRepository


@lru_cache(maxsize=1)
def repository() -> DistributionRepository:
    return DistributionRepository()
