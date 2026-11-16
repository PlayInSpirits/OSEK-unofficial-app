import unittest

from sdo_reborn.client import SDOError, parse_courses, parse_events, safe_url


class ParserTests(unittest.TestCase):
    def test_courses_deduplicate_and_ignore_other_hosts(self):
        html = '''<a href="/course/view.php?id=42" title="Безопасность">курс</a>
                  <a href="https://sdo.i-spo.ru/course/view.php?id=42">дубликат</a>
                  <a href="https://example.com/course/view.php?id=99">чужой</a>'''
        courses = parse_courses(html)
        self.assertEqual([(course.id, course.title) for course in courses], [(42, "Безопасность")])

    def test_calendar_event(self):
        html = '''<div data-type="event" data-event-id="7" data-event-eventtype="bbbevents">
        <h3 class="name">Семинар</h3><div><a href="/calendar/view.php?view=day&amp;time=100">Завтра</a>, 08:50</div>
        <div class="description-content">Ссылка на занятие</div></div>'''
        events = parse_events(html)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Семинар")
        self.assertEqual(events[0].when, "Завтра , 08:50")
        self.assertEqual(events[0].kind, "bbbevents")

    def test_course_uses_program_id_from_navigation(self):
        html = '''<a href="/course/view.php?id=9">Мой курс</a>
                  <a href="/course/view.php?id=1&amp;pid=8655">Меню программы</a>'''
        self.assertEqual(parse_courses(html)[0].url, "https://sdo.i-spo.ru/course/view.php?id=9&pid=8655")

    def test_reject_external_url(self):
        with self.assertRaises(SDOError):
            safe_url("https://example.com/steal")


if __name__ == "__main__":
    unittest.main()
