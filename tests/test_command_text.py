import unittest

from monkey.command_text import split, join


class CommandText(unittest.TestCase):
    def test_windows_identifiers_are_preserved(self):
        command=r'''/build --path "C:\Work Space\O'Connor" --name APP-123 --hash abc123 --equals '{"text":"A\\B"}' '''
        values=split(command,windows=True)
        self.assertEqual(values[2],"C:\\Work Space\\O'Connor")
        self.assertEqual(values[4:8],['APP-123','--hash','abc123','--equals'])
        self.assertEqual(values[-1],r'{"text":"A\\B"}')
        self.assertEqual(split(join(values,windows=True),windows=True),values)
        self.assertEqual(split(r'/build --path C:\work\project',windows=True)[-1],r'C:\work\project')

    def test_both_modes_keep_quoted_values_and_literal_shell_text(self):
        values=['/tool','--note',"O'Connor said \"review\"",'$(touch unwanted)','',r'abc\def','#123']
        for windows in (False,True):
            self.assertEqual(split(join(values,windows=windows),windows=windows),values)
        self.assertEqual(split(r'--path /a\ b',windows=False),['--path','/a b'])
        for windows in (False,True):
            with self.assertRaises(ValueError):
                split('"unfinished',windows=windows)
