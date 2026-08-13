from unittest import TestCase

from scripts.achievement_scout import parse


class AchievementScoutTests(TestCase):
    def test_parses_and_deduplicates_visible_achievements(self):
        page = (
            '<img alt="Achievement: YOLO">'
            '<img alt="Achievement: Quickdraw">'
            '<img alt="Achievement: YOLO">'
        )
        self.assertEqual(parse(page), ["Quickdraw", "YOLO"])
