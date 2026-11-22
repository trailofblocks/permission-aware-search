import unittest

from pydantic import ValidationError

from scope_search.contracts import TenantSpec
from scope_search.store import ResultIds


class ContractTests(unittest.TestCase):
    def test_import_requires_an_admin_and_known_groups(self):
        base = {"tenant_id": "example", "name": "Example", "users": [{"handle": "admin", "name": "Admin"}]}
        with self.assertRaises(ValidationError):
            TenantSpec.model_validate(base)
        base["users"][0].update(role="admin", groups=["unknown"])
        with self.assertRaises(ValidationError):
            TenantSpec.model_validate(base)

    def test_cache_preserves_empty_searches_and_evicts_least_recently_used(self):
        cache = ResultIds(capacity=2)
        cache.save(("first",), ())
        cache.save(("second",), ("slug",))
        self.assertEqual(cache.get(("first",)), ())
        cache.save(("third",), ("other",))
        self.assertIsNone(cache.get(("second",)))
