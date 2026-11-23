import unittest

from seam_survey.geometry import (
    convex_difference,
    convex_intersection,
    intersection_area_with_union,
    polygon_area,
    polygon_min_distance,
    polygon_union_area,
    shape_overlap,
    uncovered_area,
)


SQ = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
RIGHT = [(10.0, 0.0), (20.0, 0.0), (20.0, 10.0), (10.0, 10.0)]
OVERLAP = [(5.0, 0.0), (15.0, 0.0), (15.0, 10.0), (5.0, 10.0)]
INSIDE = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]


class GeometryTests(unittest.TestCase):
    def test_adjacent_squares_union_is_exact(self):
        self.assertAlmostEqual(polygon_union_area([SQ, RIGHT]), 200.0, places=9)

    def test_triple_overlap_union(self):
        a = SQ
        b = OVERLAP
        c = [(8.0, 0.0), (18.0, 0.0), (18.0, 10.0), (8.0, 10.0)]
        # Union spans x 0..18 = 180 m2.
        self.assertAlmostEqual(polygon_union_area([a, b, c]), 180.0, places=9)

    def test_union_with_disjoint_island(self):
        island = [(30.0, 0.0), (32.0, 0.0), (32.0, 2.0), (30.0, 2.0)]
        self.assertAlmostEqual(polygon_union_area([SQ, island]), 104.0, places=9)

    def test_intersection_area_with_union_is_capped(self):
        # Two overlapping covers must not double-count.  OVERLAP covers x 5..15
        # and the second covers x 8..18; together they cover x 5..18, i.e. half
        # of SQ, and their mutual overlap is counted once.
        second = [(8.0, 0.0), (18.0, 0.0), (18.0, 10.0), (8.0, 10.0)]
        covered = intersection_area_with_union(SQ, [OVERLAP, second])
        self.assertAlmostEqual(covered, 50.0, places=9)
        # Repeating the same cover adds nothing.
        repeated = intersection_area_with_union(SQ, [OVERLAP, OVERLAP, second])
        self.assertAlmostEqual(repeated, 50.0, places=9)

    def test_uncovered_area_of_full_cover_is_zero(self):
        self.assertAlmostEqual(uncovered_area(SQ, RIGHT), 100.0, places=9)
        self.assertAlmostEqual(uncovered_area(SQ, SQ), 0.0, places=9)

    def test_difference_pieces_are_disjoint_and_complete(self):
        pieces = convex_difference(SQ, INSIDE)
        self.assertGreaterEqual(len(pieces), 4)
        total = sum(polygon_area(piece) for piece in pieces)
        self.assertAlmostEqual(total, 96.0, places=9)
        for index, first in enumerate(pieces):
            for second in pieces[index + 1 :]:
                self.assertAlmostEqual(
                    intersection_area_with_union(first, [second]), 0.0, places=9
                )

    def test_min_distance_adjacent_is_zero(self):
        self.assertAlmostEqual(polygon_min_distance(SQ, RIGHT), 0.0, places=9)

    def test_min_distance_gap(self):
        shifted = [(11.0, 0.0), (21.0, 0.0), (21.0, 10.0), (11.0, 10.0)]
        self.assertAlmostEqual(polygon_min_distance(SQ, shifted), 1.0, places=9)

    def test_zero_area_intersection(self):
        self.assertEqual(convex_intersection([SQ, RIGHT]), ())

    def test_polyline_versus_polygon_tolerance(self):
        wall = [(4.0, 4.0), (6.0, 4.0)]
        self.assertTrue(shape_overlap(wall, SQ))
        outside = [(20.0, 20.0), (21.0, 20.0)]
        self.assertFalse(shape_overlap(outside, SQ))
        near = [(10.02, 5.0), (11.0, 5.0)]
        self.assertTrue(shape_overlap(near, SQ, tolerance=0.05))
        self.assertFalse(shape_overlap(near, SQ, tolerance=0.001))


if __name__ == "__main__":
    unittest.main()
