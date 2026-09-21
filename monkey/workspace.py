"""General coding workspace tools with durable preimages and exact read-back."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

from .common import Refused, digest, identity, require
from .project_tools import ProjectFiles, relative, run_test
from .sml import put

GUIDANCE={'AGENTS.md','CLAUDE.md'}


class Correction(Exception):
    """An ordinary input mismatch the agent can correct within the same scope."""


class Workspace:
    def __init__(self,root,audit,archive, *, build_recipe=None, build_root=None):
        self.files=ProjectFiles(root,audit=audit)
        self.root,self.audit,self.archive=self.files.root,audit,Path(archive)
        self.build_recipe,self.build_root=build_recipe,build_root

    def raw(self,name):
        with self.files.parent(name) as (parent,leaf):
            try: fd=os.open(leaf,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            except FileNotFoundError: return None,None
            with os.fdopen(fd,'rb') as stream:
                meta=os.fstat(stream.fileno())
                require(stat.S_ISREG(meta.st_mode) and meta.st_nlink==1 and meta.st_size<=2000000,'Select an unlinked source file of at most 2 MB')
                raw=stream.read(2000001)
                after=os.fstat(stream.fileno())
                require(len(raw)<=2000000 and (meta.st_dev,meta.st_ino,meta.st_size,meta.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'Source changed while reading')
            return raw,stat.S_IMODE(meta.st_mode)

    def read(self,name,start=1,end=200):
        relative(name)
        require(type(start) is int and type(end) is int and 1<=start<=end and end-start<400,'Read a bounded line range')
        self.audit.observe('file.read.requested',{'path':str(self.root/name),'start_line':start,'end_line':end})
        raw,mode=self.raw(name)
        if raw is None: raise Correction('File does not exist: '+name)
        try: text=raw.decode('utf-8')
        except UnicodeError: raise Refused('Source editing requires UTF-8 text') from None
        lines=text.splitlines(keepends=True)
        result={'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'text':''.join(lines[start-1:end])[:24000],
            'start_line':start,'end_line':min(end,len(lines)),'total_lines':len(lines),'bytes':len(raw),'mode':mode}
        result['truncated']=start>1 or end<len(lines) or len(''.join(lines[start-1:end]))>24000
        self.audit.observe('file.read.completed',{k:v for k,v in result.items() if k!='text'})
        return result

    def mkdir(self,name):
        parts=relative(name)
        require(not any(p in GUIDANCE for p in parts),'Guidance files cannot become directories')
        self.audit.observe('directory.create.requested',{'path':str(self.root/name)})
        created=[]
        with self.files.parent('.monkey-root-anchor') as (parent,_):
            fd=os.dup(parent)
            try:
                for index,part in enumerate(parts):
                    try:
                        os.mkdir(part,0o755,dir_fd=fd)
                        os.fsync(fd)
                        created.append('/'.join(parts[:index+1]))
                    except FileExistsError: pass
                    nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    os.close(fd);fd=nxt
            finally: os.close(fd)
        result={'path':name,'created':created,'changed':bool(created),
            'note':'Directories created.' if created else 'The directory already exists. No directory was changed.'}
        self.audit.observe('directory.create.completed',{**result,'path':str(self.root/name)})
        return result

    def tree(self):
        return self.files.listing()

    def search(self,query):
        require(type(query) is str and 0<len(query)<=200,'Use a bounded literal source search')
        matches=[]
        inspected=[]
        listing=self.tree()
        for name in listing['files']:
            try: raw,_=self.raw(name)
            except (Refused,OSError): continue
            if raw is None or b'\0' in raw: continue
            inspected.append({'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
            value=raw.decode('utf-8',errors='replace')
            for number,line in enumerate(value.splitlines(),1):
                if query in line: matches.append({'path':name,'line':number,'text':line[:300]})
                if len(matches)>=60: break
            if len(matches)>=60: break
        self.audit.observe('source.searched',{'root':str(self.root),'query':query,'files_inspected':inspected,'matches':len(matches)})
        return {'matches':matches,'truncated':listing['truncated'] or len(matches)>=60}

    def mutate(self,name,content,expected, *, remove=False):
        parts=relative(name)
        require(parts[-1] not in GUIDANCE,'A coding tool cannot edit its own project guidance')
        require(content is None if remove else type(content) is str and len(content.encode())<=250000,'Use bounded UTF-8 source content')
        if len(parts)>1: self.mkdir('/'.join(parts[:-1]))
        before,mode=self.raw(name)
        before_hash=hashlib.sha256(before).hexdigest() if before is not None else None
        require(before_hash==expected,'Source changed after its last recorded read; inspect it before editing')
        require(not remove or before is not None,'Cannot remove an absent file')
        if not remove and before==content.encode():
            self.audit.observe('file.write.unchanged',{'path':str(self.root/name),'sha256':before_hash})
            return {'path':name,'before_sha256':before_hash,'after_sha256':before_hash,'changed':False,'removed':False,
                'note':'The requested content already exists. No file was changed.'}
        action=identity('fileaction_')
        backup={'schema_version':1,'action_id':action,'path':name,'before_sha256':before_hash,'mode':mode,
            'text':before.decode('utf-8') if before is not None else None}
        put(self.archive/(action+'.json'),backup)
        new=content.encode() if content is not None else None
        after_hash=hashlib.sha256(new).hexdigest() if new is not None else None
        self.audit.observe('file.delete.requested' if remove else 'file.write.requested',{'action_id':action,'path':str(self.root/name),
            'before_sha256':before_hash,'proposed_sha256':after_hash,'preimage':str(self.archive/(action+'.json')),'preimage_hash':digest(backup)})
        with self.files.parent(name) as (parent,leaf):
            current,_=self.raw(name)
            require((hashlib.sha256(current).hexdigest() if current is not None else None)==expected,'Concurrent source edit detected')
            if remove:
                os.unlink(leaf,dir_fd=parent)
            else:
                temporary='.monkey-write-'+identity()
                fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode or 0o644,dir_fd=parent)
                try:
                    with os.fdopen(fd,'wb') as stream:
                        stream.write(new);stream.flush();os.fsync(stream.fileno())
                    os.rename(temporary,leaf,src_dir_fd=parent,dst_dir_fd=parent)
                finally:
                    with contextlib.suppress(FileNotFoundError): os.unlink(temporary,dir_fd=parent)
            os.fsync(parent)
        actual,_=self.raw(name)
        require(actual==new,'File effect read-back mismatch')
        result={'action_id':action,'path':name,'before_sha256':before_hash,'after_sha256':after_hash,
            'preimage':str(self.archive/(action+'.json')),'preimage_hash':digest(backup),'removed':remove,'changed':True}
        self.audit.observe('file.delete.completed' if remove else 'file.write.completed',{**result,'path':str(self.root/name)})
        return result

    def patch(self,name,old,new,expected):
        require(type(old) is str and old and type(new) is str,'A patch needs exact old and new source text')
        raw,_=self.raw(name)
        require(raw is not None and hashlib.sha256(raw).hexdigest()==expected,'Read the existing file before patching it')
        text=raw.decode('utf-8')
        if text.count(old)!=1: raise Correction('Patch text must match exactly once; read the correct source range')
        return self.mutate(name,text.replace(old,new,1),expected)

    def executables(self):
        if self.build_recipe:
            return dict(self.build_recipe['executables'])
        # Invoke the installed interpreter itself; Monkey's private venv is not
        # part of a project scope and must not be needed to start Python.
        interpreter=str(Path(sys.executable).resolve())
        result={name:interpreter for name in ('python','python3','python'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))}
        for name in ('node','ruby','perl','clang','swiftc','git','make','cmake','go','rustc','cargo','npm','pytest','pdftotext'):
            path=shutil.which(name)
            if path: result[name]=path
        return result

    async def run(self,argv,scratch):
        if self.build_recipe:
            import asyncio
            from .build_runner import BuildRunner, import_source_changes
            require(self.build_root is not None,'Captured builder evidence directory is missing')
            runner = await asyncio.to_thread(BuildRunner,self.build_recipe,self.build_root,self.audit)
            operation = 'build_' + hashlib.sha256(str(scratch).encode()).hexdigest()[:32]
            result = await runner.run(self.root,argv,operation)
            if result['complete']:
                applied = await import_source_changes(self,result)
                result.update(applied)
                # A command that only checked source is still useful work.
                result.pop('changed',None)
            from .verification import command_check
            return {k:v for k,v in {**result,'check_assessment':command_check(result)}.items()
                    if k not in {'changes','source_before','source_after'}}
        require(type(argv) is list and 1<=len(argv)<=32 and all(type(a) is str and '\0' not in a and len(a)<3000 for a in argv),'Use an executable and literal argument list')
        require(not any(word in ' '.join(argv).lower() for word in ('chrome','chromium','chromedriver','selenium','playwright','puppeteer')),'Browser runtimes are unavailable under the workspace tooling policy')
        available=self.executables()
        executable=available.get(argv[0],argv[0])
        require(executable in available.values(),'Choose an installed coding executable from the recorded tool catalog')
        if Path(executable).name=='git':
            require(len(argv)>1 and argv[1] in {'diff','status','ls-files','show','log'},'Repository publishing and configuration require explicit operator action')
        require(not any(a in {'install','add','update','upgrade','publish','push','deploy','login'} for a in argv[1:]),'Dependency acquisition and external deployment need a separately reviewed operation')
        result=await run_test(self.root,[executable,*argv[1:]],scratch,audit=self.audit)
        from .verification import command_check
        return {**result,'execution_boundary':'Current native backend: one process; source read-only; scratch writable; no network',
            'check_assessment':command_check(result),
            'note':'A command exit is recorded evidence. Validate that its checks establish the requested outcome.'}
