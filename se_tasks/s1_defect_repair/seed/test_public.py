import unittest

from versioning import is_compatible


class CompatibilityTest(unittest.TestCase):
    def test_equal(self):
        self.assertTrue(is_compatible("1.2.3", "1.2.3"))

    def test_larger_minor(self):
        self.assertTrue(is_compatible("1.3", "1.2"))

    def test_smaller_major(self):
        self.assertFalse(is_compatible("1.9", "2.0"))


if __name__ == "__main__":
    unittest.main()
