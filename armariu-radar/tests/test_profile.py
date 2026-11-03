import unittest
from datetime import date

from radar import profile, refi_ml


class ProfileTests(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(profile._iso("11/09/2012"), "2012-09-11")
        self.assertEqual(profile._iso(""), "")

    def test_regions(self):
        self.assertEqual(refi_ml.AREA_REGION["HG"], "Yorkshire and North East")
        self.assertEqual(refi_ml.AREA_REGION["EC"], "London")

    def test_train_dates_close_before_test(self):
        for d in refi_ml._train_cutoffs(refi_ml.TEST[0]):
            self.assertLessEqual((d - date(1, 1, 1)).days + refi_ml.WINDOW, (refi_ml.TEST[0] - date(1, 1, 1)).days)


if __name__ == "__main__":
    unittest.main()
