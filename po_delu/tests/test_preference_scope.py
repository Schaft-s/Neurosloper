import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preferences import add, attach


class PreferenceScope(unittest.TestCase):
    def test_word_edges_and_exceptions(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PO_DELU_MEMORY_DB':str(Path(tmp)/'rules.db')}):
            add('a','example','акт','документ')
            add('a','exception','«акт»')
            text='Контакт и факт. Подпишите акт. В цитате «акт» оставьте буквально.'
            job={'blocks':[{'id':'0','text':text,'edit':True}]}
            found=attach(job,'a')['personal_matches']
            self.assertEqual([(x['kind'],x['start']) for x in found],[('example',text.index('акт. В')),('exception',text.index('«'))])
            self.assertFalse(attach(job,'b')['personal_matches'])
