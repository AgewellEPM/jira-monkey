"""Interpret known empty test runs without inventing outcome coverage."""
import re


def command_check(result):
    if result.get('complete') is False:
        return {'usable':False,'reason':'The command has incomplete execution or collection evidence; inspect its recorded stop reason.'}
    if result.get('timed_out'):
        return {'usable':False,'reason':'The command exceeded its time limit; partial output cannot establish success.'}
    if result.get('exit_code') != 0:
        return {'usable':False,'reason':'The command did not exit successfully.'}
    empty = re.search(r'(?m)^Ran 0 tests? in [0-9.]+s\s*$',result.get('output',''))
    if empty:
        return {'usable':False,'reason':'unittest ran zero tests; select the real test directory or module.','executed_tests':0}
    return {'usable':True,'reason':'Successful command; outcome coverage still requires inspection.'}


def successful_command(result):
    return command_check(result)['usable']
