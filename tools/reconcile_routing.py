"""Reconcile documented no-endpoint responses in three historical routing experiments."""
import json
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LEDGER=ROOT/'evaluation/v1/runs/billing/budget.sqlite'
# v1 check-fixed / check-cascade were repeated with supported routing parameters.
# v2 case probes used a model without support for temperature; the corrected
# experiment has its own run id. No tools in these stages execute remote plugins.
ROUTING_RUNS=('check-fixed','check-cascade','v2-dev-cases-search')
BASIS='No endpoints matched the supplied parameters. No generation ID, usage or auxiliaries. https://openrouter.ai/pricing'

def reconcile():
    with sqlite3.connect(LEDGER,timeout=45) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS routing_releases(call_id INTEGER PRIMARY KEY, basis TEXT NOT NULL)')
        totals={}
        for run in ROUTING_RUNS:
            params=(run,)
            rows=db.execute("""SELECT id,reserve FROM calls WHERE substr(tag,1,instr(tag,'/')-1)=?
                AND status='404' AND generation_id IS NULL AND actual IS NULL AND prompt_tokens IS NULL
                AND completion_tokens IS NULL""",params).fetchall()
            db.executemany('INSERT OR IGNORE INTO routing_releases VALUES(?,?)',[(i,BASIS) for i,_ in rows])
            totals[run]={'documented_routing_errors':len(rows),'reserved_usd':sum(v for _,v in rows)}
        print(json.dumps(totals,indent=2))
if __name__=='__main__':reconcile()
