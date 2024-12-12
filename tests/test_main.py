import unittest
from src.main import main_function

class TestMainFunction(unittest.TestCase):
    def test_function(self):
        expected_value = None
        self.assertEqual(main_function(), expected_value)

if __name__ == '__main__':
    unittest.main()
