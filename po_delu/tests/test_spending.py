import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx
from spending import BudgetTransport, CEILING, MODELS, ORIGIN


class Budget(unittest.TestCase):
    def test_unsettled_request_reserves_credit_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / 'budget.sqlite'
            BudgetTransport(ledger, 'first')
            with sqlite3.connect(ledger) as connection:
                connection.execute('INSERT INTO calls(tag,reserve,status) VALUES(?,?,?)', ('earlier', CEILING, 'reserved'))
            retry = BudgetTransport(ledger, 'next')
            request = httpx.Request('POST', ORIGIN, json={'model': MODELS[0], 'messages': []})
            with self.assertRaisesRegex(ValueError, 'spending limit'):
                asyncio.run(retry.handle_async_request(request))
            with sqlite3.connect(ledger) as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM calls').fetchone()[0], 1)

    def test_other_upstream_fails_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = BudgetTransport(Path(tmp) / 'budget.sqlite', 'invalid')
            request = httpx.Request('POST', 'https://invalid.example/chat/completions', json={})
            with self.assertRaisesRegex(ValueError, 'upstream'):
                asyncio.run(gate.handle_async_request(request))
