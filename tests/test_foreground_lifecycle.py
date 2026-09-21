"""Kill an owning process during an actual SML request; check host cleanup."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(Path('/Users/lukekist/bin/kist-current').is_file(),'Installed Kist required')
class ForegroundLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_parent_crash_stops_real_sml_host_without_replay(self):
        code = '''import asyncio,sys
from pathlib import Path
from monkey.sml import SML,file_hash
root=Path(sys.argv[1])
async def effect():
 (root/'entered').write_text('actual SML capability claim reached')
 await asyncio.sleep(30)
 return {'completed':True}
async def main():
 binary='/Users/lukekist/bin/kist-current'
 await SML(binary,file_hash(binary)).execute(root/'host',effect,lambda:None,operation_id='crash-check')
asyncio.run(main())
'''
        def children(root):
            rows = subprocess.check_output(['/bin/ps','-axo','pid=,pgid=,command='],text=True)
            return [(int(bits[0]),int(bits[1]),bits[2]) for row in rows.splitlines() if str(root) in row and len(bits:=row.strip().split(None,2))==3]
        with tempfile.TemporaryDirectory(prefix='monkey-parent-crash-',dir='/private/tmp') as folder:
            root = Path(folder)
            process = await asyncio.create_subprocess_exec(sys.executable,'-c',code,str(root),cwd=Path(__file__).resolve().parents[1],
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
            try:
                async with asyncio.timeout(8):
                    while not (root/'entered').exists():
                        self.assertIsNone(process.returncode)
                        await asyncio.sleep(.03)
                before = await asyncio.to_thread(children,root)
                self.assertTrue(any('runtime_child.py' in cmd for _,_,cmd in before))
                self.assertTrue(any('kist-current --project' in cmd for _,_,cmd in before))
                process.kill()
                await process.wait()
                async with asyncio.timeout(6):
                    while await asyncio.to_thread(children,root):
                        await asyncio.sleep(.06)
                runtime = json.loads((root/'host/.kist/sml/runtime.json').read_text())
                self.assertTrue(all(t['status']!='completed' for t in runtime['tasks'].values()))
                self.assertFalse(any(r['kind']=='receipt_emitted' for r in runtime['receipts'].values()))
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                for pid,pgid,_ in await asyncio.to_thread(children,root):
                    try:
                        os.killpg(pgid,signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            self.assertEqual(await asyncio.to_thread(children,root),[])
