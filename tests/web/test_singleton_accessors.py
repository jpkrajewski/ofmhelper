"""Shared instances come from `lru_cache`d accessors, not module-level globals.

The reason is not tidiness: a module-level instance is built at *import*, and
these all bind something the environment decides (a Redis connection for the
repository cache, settings for the template environment). Import-time is before
a test fixture -- or a worker process -- has finished setting that up, which is
what `db/repositories/cached_repository.py` used to carry a whole extra
lazy-wrapper class to work around.
"""

from ofmhelpers.web.db.repositories import cached_repository as cache_module
from ofmhelpers.web.stores import approval_tokens, instagram_stats, jobs, todos
from ofmhelpers.web.stores import models as models_store
from ofmhelpers.web.templates_config import get_templates

STORES = (jobs, todos, models_store, approval_tokens, instagram_stats)


def test_every_store_hands_out_one_repository():
    for store in STORES:
        assert store._repository() is store._repository(), store.__name__


def test_templates_are_a_single_instance():
    assert get_templates() is get_templates()


def test_no_store_builds_its_repository_at_import():
    """`_repository` must be the accessor, not a pre-built instance left over
    under the old name."""
    for store in STORES:
        assert callable(store._repository), store.__name__
        assert not hasattr(store, "_repo"), store.__name__


def test_the_lazy_cache_wrapper_is_gone():
    """It existed only because repositories were built at import. Leaving it
    behind would invite the next repository to be built at import again."""
    assert not hasattr(cache_module, "LazyRepositoryCache")


def test_a_repository_binds_redis_at_construction_not_import():
    """The whole point of the accessors: by the time this runs, the test
    fixture's OFM_REDIS_URL is what the cache connects to."""
    repo = jobs._repository()
    assert isinstance(repo._cache, cache_module.RepositoryCache)
