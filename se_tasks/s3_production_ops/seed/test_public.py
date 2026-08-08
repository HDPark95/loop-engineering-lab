import unittest

from service import handle


class HandlerTest(unittest.TestCase):
    def test_small_payload(self):
        result = handle({"payload": "hello"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)

    def test_response_shape(self):
        self.assertEqual(set(handle({"payload": "x"})), {"ok", "work_units", "status"})


if __name__ == "__main__":
    unittest.main()
