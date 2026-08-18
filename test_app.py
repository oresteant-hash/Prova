import unittest
import app
class AtlasTests(unittest.TestCase):
 def test_change(self): self.assertAlmostEqual(app.change([100,110],1),10)
 def test_trend_score_range(self): self.assertIn(app.trend_score(list(range(1,254))),[25,50,75,100])
 def test_correlation(self): self.assertAlmostEqual(app.correlation(list(range(100,170)),list(range(200,270))),1,places=2)
 def test_item(self): self.assertEqual(app.item(1.23456,75,"x","2026-01-01")["value"],1.235)
if __name__=='__main__': unittest.main()
