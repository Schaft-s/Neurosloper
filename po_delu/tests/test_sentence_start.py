import unittest
from engine import assemble


class SentenceStart(unittest.TestCase):
    def test_respect_left_context(self):
        for text, original, new, expected in (
            ("Здравствуйте! По причине дождя мы остались дома.", "По причине", "из-за", "Здравствуйте! Из-за дождя мы остались дома."),
            ("Мы остались дома по причине дождя.", "по причине", "из-за", "Мы остались дома из-за дождя."),
            ("На экране появилась карточка.", "На экране", "iOS:", "iOS: появилась карточка."),
        ):
            start = text.index(original)
            spans = [{"id": "r", "start": start, "end": start + len(original)}]
            self.assertEqual(assemble(text, spans, [{"id": "r", "after": new}]), expected)
