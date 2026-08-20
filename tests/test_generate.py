import unittest
from datetime import date

import generate as g


class HumanDuration(unittest.TestCase):
    def test_years_months_days(self):
        self.assertEqual(
            g.human_duration(date(2002, 1, 19), date(2026, 8, 20)),
            "24 years, 7 months, 1 day",
        )

    def test_drops_zero_years(self):
        self.assertEqual(
            g.human_duration(date(2026, 6, 8), date(2026, 8, 20)),
            "2 months, 12 days",
        )

    def test_singular_plural(self):
        self.assertEqual(
            g.human_duration(date(2025, 7, 19), date(2026, 8, 20)),
            "1 year, 1 month, 1 day",
        )

    def test_same_day(self):
        self.assertEqual(g.human_duration(date(2026, 6, 8), date(2026, 6, 8)), "0 days")

    def test_borrow_days_from_previous_month(self):
        # 31 Jan -> 1 Mar : 1 month (Feb) + 1 day
        self.assertEqual(g.human_duration(date(2026, 1, 31), date(2026, 3, 1)), "1 month, 1 day")


class LeaderLine(unittest.TestCase):
    def test_pads_with_dots_to_width(self):
        label, dots, value = g.leader("OS", "macOS", width=20)
        self.assertEqual(label, "- OS: ")
        self.assertEqual(value, "macOS")
        self.assertEqual(len(label) + len(dots) + len(value), 20)
        self.assertTrue(set(dots) <= {".", " "})
        self.assertTrue(dots.startswith("."), dots)
        self.assertTrue(dots.endswith(" "), dots)

    def test_never_negative(self):
        label, dots, value = g.leader("A very long label", "and a very long value", width=10)
        self.assertEqual(dots, " ")


class FmtInt(unittest.TestCase):
    def test_thousands_separator(self):
        self.assertEqual(g.fmt_int(446276), "446,276")
        self.assertEqual(g.fmt_int(95), "95")


class Escape(unittest.TestCase):
    def test_escapes_xml(self):
        self.assertEqual(g.esc('<a & "b">'), "&lt;a &amp; &quot;b&quot;&gt;")


if __name__ == "__main__":
    unittest.main()


class Identity(unittest.TestCase):
    def test_my_logins_and_emails(self):
        self.assertTrue(g.is_me("Math-MO", "whatever@x.y"))
        self.assertTrue(g.is_me("Pooxie", None))
        self.assertTrue(g.is_me(None, "devclaude7@divabox.net"))
        self.assertTrue(g.is_me(None, "mathmyo@Mac-mini-de-Matheo.local"))
        self.assertTrue(g.is_me(None, "m.riba@DIVABOX.NET"))
        self.assertTrue(g.is_me(None, "291725608+Math-MO@users.noreply.github.com"))

    def test_colleagues_excluded(self):
        self.assertFalse(g.is_me("adam1999", "tardyadam2@gmail.com"))
        self.assertFalse(g.is_me(None, "a.tardy@DIVABOX.NET"))
        self.assertFalse(g.is_me("IsulaClim-dev", None))
        self.assertFalse(g.is_me(None, "root@srv1765253.hstgr.cloud"))
        self.assertFalse(g.is_me(None, None))
