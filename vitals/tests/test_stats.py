"""The statistics, including the empty and single-value cases."""

import unittest

from vitals import stats


class Small(unittest.TestCase):
    def test_median(self):
        self.assertEqual(stats.median([3, 1, 2]), 2)
        self.assertEqual(stats.median([1, 2, 3, 4]), 2.5)
        self.assertIsNone(stats.median([]))
        self.assertEqual(stats.median([5, None]), 5)

    def test_quantile(self):
        self.assertEqual(stats.quantile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(stats.quantile([7], 0.9), 7.0)
        self.assertIsNone(stats.quantile([], 0.5))
        self.assertEqual(stats.quantile([1, 2], 1.0), 2.0)

    def test_share_refuses_to_divide_by_nothing(self):
        self.assertIsNone(stats.share(3, 0))
        self.assertEqual(stats.share(1, 4), 0.25)

    def test_gini(self):
        self.assertEqual(stats.gini([5, 5, 5, 5]), 0.0)
        self.assertGreater(stats.gini([0, 0, 0, 100]), 0.7)
        self.assertIsNone(stats.gini([4]))
        self.assertIsNone(stats.gini([0, 0]))

    def test_bus_factor(self):
        self.assertEqual(stats.bus_factor([10, 1, 1, 1]), 1)
        self.assertEqual(stats.bus_factor([1, 1, 1, 1]), 2)
        self.assertIsNone(stats.bus_factor([0, 0]))

    def test_top_share(self):
        self.assertAlmostEqual(stats.top_share([1] * 20, n=10), 0.5)
        self.assertIsNone(stats.top_share([]))

    def test_trend_needs_enough_history(self):
        self.assertIsNone(stats.trend([1, 2, 3]))
        self.assertAlmostEqual(stats.trend([10, 10, 10, 5, 5, 5]), -0.5)
        self.assertIsNone(stats.trend([0, 0, 0, 1, 1, 1]))


if __name__ == "__main__":
    unittest.main()
