"""Outcome checks must reject plausible tests and incorrect ticket summaries."""
from pathlib import Path
import runpy
import tempfile
import unittest

from monkey.demo import demo_app
from monkey.workspace import Workspace


CHECK = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/local_workflow_cases.py'))['check_sprint_summary']
SOURCE = '''import csv, io, json, sys
def summarize_tickets(text):
    reader = csv.DictReader(io.StringIO(text))
    statuses = ('todo', 'in_progress', 'done', 'blocked')
    if not set(('key', 'title', 'status', 'points', 'owner')) <= set(reader.fieldnames or []):
        raise ValueError('Missing columns')
    result = {'tickets': 0, 'points': 0, 'by_status': {s: {'tickets': 0, 'points': 0} for s in statuses}, 'needs_attention': []}
    seen = set()
    for row in reader:
        key, status = row['key'], row['status']
        if not key or key in seen or status not in statuses: raise ValueError('Invalid identity or status')
        seen.add(key)
        points = int(row['points']) if row['points'] else 0
        if points < 0: raise ValueError('Negative points')
        result['tickets'] += 1
        result['points'] += points
        result['by_status'][status]['tickets'] += 1
        result['by_status'][status]['points'] += points
        if status == 'blocked' or not row['owner'].strip(): result['needs_attention'].append(key)
    return result
if __name__ == '__main__':
    with open(sys.argv[1]) as source: print(json.dumps(summarize_tickets(source.read())))
'''
WEAK_TEST = '''import unittest
from sprint_summary import summarize_tickets
class Authored(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summarize_tickets('key,title,status,points,owner\\n')['tickets'], 0)
'''


class WorkflowOracle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.app, _ = demo_app(self.root / 'state')

    async def asyncTearDown(self):
        await self.app.close()
        self.temp.cleanup()

    async def check(self, source, test=WEAK_TEST):
        root = self.root / 'workspace'; root.mkdir()
        files = Workspace(root, self.app.audit, self.root / 'preimages')
        async with self.app.audit.run(None, 'oracle-fixture'):
            files.mutate('sprint_summary.py', source, None)
            files.mutate('test_sprint_summary.py', test, None)
            result = await CHECK(files, self.root / 'checks')
        self.app.audit.verify()
        return result

    async def test_correct_contract_and_real_cli_pass(self):
        result = await self.check(SOURCE)
        self.assertTrue(result['passed'], result)
        self.assertTrue(result['cli_output_matches'])

    async def test_weak_authored_tests_cannot_hide_duplicate_issue_keys(self):
        result = await self.check(SOURCE.replace(' or key in seen', ''))
        self.assertEqual(result['authored_tests']['exit_code'], 0)
        self.assertNotEqual(result['independent_contract']['exit_code'], 0)
        self.assertFalse(result['passed'])

    async def test_cli_contract_requires_real_json_output(self):
        result = await self.check(SOURCE.replace('print(json.dumps(summarize_tickets(source.read())))',
                                                "print('Finished successfully')"))
        self.assertEqual(result['independent_contract']['exit_code'], 0)
        self.assertFalse(result['cli_output_matches'])
        self.assertFalse(result['passed'])

    async def test_zero_authored_tests_do_not_qualify(self):
        result = await self.check(SOURCE, '# No test cases\n')
        self.assertEqual(result['independent_contract']['exit_code'], 0)
        self.assertFalse(result['passed'])
