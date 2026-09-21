"""REPL tokenization only. No shell expansion or execution takes place."""
import os
import shlex


def split(text, *, windows=None):
    if windows is None:
        windows=os.name=='nt'
    lexer=shlex.shlex(text,posix=True)
    lexer.whitespace_split=True
    lexer.commenters=''
    # Windows drive paths use literal backslashes. Both quote styles group a
    # path containing spaces; REPL syntax does not evaluate PowerShell or cmd.
    if windows:
        lexer.escape=''
    return list(lexer)


def join(values, *, windows=None):
    if windows is None:
        windows=os.name=='nt'
    if not windows:
        return shlex.join(values)
    # A single quote is represented by adjoining quoted pieces, without
    # treating backslashes in a drive path as escapes.
    return ' '.join("'"+value.replace("'", "'\"'\"'")+"'" for value in values)
