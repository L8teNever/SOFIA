import unittest

from backend.version import client_needs_update


class ClientNeedsUpdateTests(unittest.TestCase):
    def test_same_commit_is_current(self):
        self.assertFalse(client_needs_update("abc1234", "abc1234def"))

    def test_different_commit_needs_update(self):
        self.assertTrue(client_needs_update("abc1234", "def5678"))

    def test_process_restart_placeholder_does_not_fake_update(self):
        self.assertFalse(client_needs_update("__COMMIT__", "abc1234"))
        self.assertFalse(client_needs_update("abc1234", "main"))
        self.assertFalse(client_needs_update(None, "abc1234"))


if __name__ == "__main__":
    unittest.main()
