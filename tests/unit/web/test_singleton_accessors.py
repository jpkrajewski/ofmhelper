from ofmhelpers.db import cached_repository as cache_module
from ofmhelpers.db.applications import store as applications
from ofmhelpers.db.jobs import store as jobs
from ofmhelpers.web.templates_config import get_templates

STORES = (jobs, applications)


def test_every_store_hands_out_one_repository():
    for store in STORES:
        assert store._repository() is store._repository(), store.__name__


def test_templates_are_a_single_instance():
    assert get_templates() is get_templates()


def test_no_store_builds_its_repository_at_import():
    for store in STORES:
        assert callable(store._repository), store.__name__
        assert not hasattr(store, "_repo"), store.__name__


def test_the_lazy_cache_wrapper_is_gone():
    assert not hasattr(cache_module, "LazyRepositoryCache")


def test_a_repository_binds_redis_at_construction_not_import():
    repo = jobs._repository()
    assert isinstance(repo._cache, cache_module.RepositoryCache)
