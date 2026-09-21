"""Fixed work contracts and separate outcome checks for actual local models.

The objective is shown to the model. Oracle source and its particular examples
are withheld until the worker settles. This is development qualification, not
an adversarial evaluation or independent third-party attestation.
"""

CASES = {
    'calculator': {'objective':
        'Create a tiny Python calculator in calc.py exposing add(a, b), returning the sum. '
        'Create test_calc.py using standard-library unittest with positive, negative and zero '
        'cases. Run the tests with the installed python alias, inspect the real result, '
        'and finish with a concise evidence-backed summary. This is a private local fixture; '
        'use no external service, network access, dependency installation or UI automation.'},
    'sprint_summary': {'objective':
        'Build a reusable sprint CSV summarizer in sprint_summary.py with a function '
        'summarize_tickets(csv_text). Input is CSV with columns key,title,status,points,owner '
        '(column order may vary). Use the standard CSV parser so quoted commas and newlines work. '
        'Statuses are exactly todo, in_progress, done, blocked. Points must be nonnegative '
        'whole numbers; an empty points cell means zero. Return a dictionary with tickets '
        '(total row count), points (total points), by_status (all four statuses, each with '
        'tickets and points totals, including zero totals), and needs_attention (issue keys '
        'in input order for blocked tickets or tickets whose owner is empty or whitespace). '
        'An empty header-only CSV returns zero totals. Reject missing required columns, '
        'empty or duplicate issue keys, unknown statuses, and negative, fractional or '
        'nonnumeric points with ValueError. Preserve issue keys exactly; never repair them. '
        'Also support python sprint_summary.py INPUT.csv, printing only the resulting JSON '
        'to stdout for a valid file. Write test_sprint_summary.py with standard-library unittest, '
        'including valid summaries and rejected input. Run it using the python alias and inspect '
        'the actual output before finishing. Use only this private workspace and installed '
        'standard-library tools; no network, dependencies, service writes or UI automation.'},
}

SPRINT_CHECK = '''import csv
import io
import unittest
from sprint_summary import summarize_tickets

STATUSES = ('todo', 'in_progress', 'done', 'blocked')
def document(rows, columns=('key', 'title', 'status', 'points', 'owner')):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(columns)
    writer.writerows(rows)
    return output.getvalue()

class SprintContract(unittest.TestCase):
    def test_empty_and_all_status_buckets(self):
        self.assertEqual(summarize_tickets(document([])), {'tickets': 0, 'points': 0,
            'by_status': {s: {'tickets': 0, 'points': 0} for s in STATUSES}, 'needs_attention': []})

    def test_quoted_text_counts_and_attention_order(self):
        text = document([
            ['APP-318', 'Quoted, title', 'todo', '3', 'Morgan'],
            ['APP-104', 'Two\\nlines', 'blocked', '8', 'Riley'],
            ['APP-771', 'Review', 'done', '', '  '],
            ['APP-510', 'Build', 'in_progress', '13', 'Sam'],
            ['APP-205', 'Waiting', 'blocked', '0', ''],
            ['APP-620', 'Done', 'done', '2', 'Avery']])
        self.assertEqual(summarize_tickets(text), {'tickets': 6, 'points': 26,
            'by_status': {'todo': {'tickets': 1, 'points': 3},
                'in_progress': {'tickets': 1, 'points': 13},
                'done': {'tickets': 2, 'points': 2}, 'blocked': {'tickets': 2, 'points': 8}},
            'needs_attention': ['APP-104', 'APP-771', 'APP-205']})

    def test_reordered_columns_and_exact_identifiers(self):
        text = document([['blocked', '  APP-009  ', 'Z', '5', 'X']],
                        ('status', 'key', 'owner', 'points', 'title'))
        self.assertEqual(summarize_tickets(text)['points'], 5)
        self.assertEqual(summarize_tickets(text)['needs_attention'], ['  APP-009  '])
        text = document([['app-9', 'X', 'blocked', '', ''], ['APP-9', 'Y', 'todo', '1', '']])
        self.assertEqual(summarize_tickets(text)['needs_attention'], ['app-9', 'APP-9'])

    def test_invalid_inputs(self):
        invalid = [
            document([], ('key', 'title', 'status', 'points')),
            document([['', 'X', 'todo', '1', 'O']]),
            document([['APP-3', 'X', 'todo', '1', 'O'], ['APP-3', 'Y', 'done', '2', 'O']]),
            document([['APP-3', 'X', 'finished', '1', 'O']]),
        ]
        invalid += [document([['APP-3', 'X', 'todo', points, 'O']])
                    for points in ('-3', '1.5', 'many', 'NaN')]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError): summarize_tickets(text)

suite = unittest.defaultTestLoader.loadTestsFromTestCase(SprintContract)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun == 4 else 1)
'''


async def check_sprint_summary(files, root):
    import json
    from monkey.verification import successful_command
    authored = await files.run(['python', '-m', 'unittest', '-v', 'test_sprint_summary'],
                               root / 'authored-test-check')
    # Keep command arguments inside the normal tool bound. Add the withheld
    # checker only after model work settles, through the traced file tool.
    files.mutate('_qualification_contract.py', SPRINT_CHECK, None)
    independent = await files.run(['python', '_qualification_contract.py'], root / 'independent-contract-check')
    # This checker input is added only after the model has finished. It is a
    # real workspace file so the CLI path goes through ordinary file admission.
    name = 'qualification-sprint-input.csv'
    files.mutate(name, 'key,title,status,points,owner\nOPS-72,"A, B",blocked,7,\n', None)
    cli = await files.run(['python', 'sprint_summary.py', name], root / 'cli-contract-check')
    expected = {'tickets': 1, 'points': 7,
        'by_status': {s: {'tickets': int(s == 'blocked'), 'points': 7 if s == 'blocked' else 0}
                      for s in ('todo', 'in_progress', 'done', 'blocked')}, 'needs_attention': ['OPS-72']}
    try:
        cli_matches = json.loads(cli.get('output', '')) == expected
    except (TypeError, ValueError):
        cli_matches = False
    return {'performed': True, 'authored_tests': authored, 'independent_contract': independent,
        'cli_contract': cli, 'cli_output_matches': cli_matches,
        'passed': all(successful_command(r) for r in (authored, independent, cli)) and cli_matches,
        'scope': 'Authored tests, separate fixed CSV cases and actual CLI JSON output; native command containment.'}
