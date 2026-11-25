import unittest
from minimal_edit import recycled_wings

class RetainedStyle(unittest.TestCase):
    def test_reordering_metaphor_keeps_the_issue(self):
        issue={'pattern':'Метафора вместо рабочего сообщения'}
        before='Зажигаем новую звезду проекта К-9 — готовим опытную сборку.'
        swapped='Готовим опытную сборку — новую звезду проекта К-9.'
        self.assertTrue(recycled_wings(before,swapped,issue))
        self.assertFalse(recycled_wings(before,'Готовим опытную сборку для К-9.',issue))
        self.assertFalse(recycled_wings(before,swapped,{'pattern':'Порядок изложения'}))
