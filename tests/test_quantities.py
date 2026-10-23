import unittest

from pdf2md_p2.quantities import equivalent


class QuantityTests(unittest.TestCase):
    def test_complete_signed_quantity_presentation_only(self):
        self.assertTrue(equivalent('\u201340\u00b0C', '-40 \u00b0C'))
        self.assertTrue(equivalent('4 \u03bcA', '4uA'))
        self.assertTrue(equivalent('4 mg', '4mg'))
        self.assertFalse(equivalent('-40\u00b0C', '+40\u00b0C'))
        self.assertFalse(equivalent('4 mg', '4 mA'))
        self.assertIsNone(equivalent('3 6', '36'))
        self.assertIsNone(equivalent('DATA_STATUS', 'DATA STATUS'))


if __name__ == '__main__':
    unittest.main()
