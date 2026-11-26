import unittest

from tools.research.dmm_unit_lookup import describe_unit


class TestDmmUnitLookup(unittest.TestCase):
    """Table decoding for tools.research.dmm_unit_lookup (no firmware needed)."""

    def test_parent_extent_lists_leaf_sizes(self):
        tables = {
            'root_extents': [],
            'units': [{'unit': 0x4092E, 'extents': [0x62]}],
            'extents': [{'header': [0, 0, 0x3F2, 0x62, 4, 0],
                         'children': [[0, 7, 0x3F0, 0x1FA400, 0x10F]]}],
        }
        described = describe_unit(tables, 0x4092E)
        extent = described[0]['extents'][0]
        self.assertEqual(extent['kind'], 'parent')
        self.assertEqual(extent['leaves'], [{'extent': 0x3F0, 'size': 0x1FA400}])

    def test_leaf_extent_resolves_owning_parent(self):
        tables = {
            'root_extents': [],
            'units': [{'unit': 0x280, 'extents': [0x35D]}],
            'extents': [{'header': [0, 0, 0x21, 0x0F, 0, 0],
                         'children': [[0, 12, 0x35D, 0x2000000, 0x10F]]}],
        }
        described = describe_unit(tables, 0x280)
        extent = described[0]['extents'][0]
        self.assertEqual(extent['kind'], 'leaf')
        self.assertEqual(extent['owners'], [{'parent': 0x0F, 'size': 0x2000000}])

    def test_absent_unit_returns_none(self):
        self.assertIsNone(describe_unit({'root_extents': [], 'units': [], 'extents': []},
                                        0x4092E))

    def test_unknown_extent_is_reported_not_invented(self):
        tables = {'root_extents': [], 'units': [{'unit': 0x1, 'extents': [0x7]}],
                  'extents': []}
        described = describe_unit(tables, 0x1)
        self.assertEqual(described[0]['extents'][0]['kind'], 'missing')
        self.assertNotIn('leaves', described[0]['extents'][0])


if __name__ == '__main__':
    unittest.main()
