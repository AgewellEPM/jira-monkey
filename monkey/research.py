"""Public text research with pinned public DNS, bounded reads and retained sources."""
import asyncio
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import shutil
import socket
import tempfile
from urllib.parse import urljoin, urlsplit, urlencode, parse_qsl, unquote_plus
import xml.etree.ElementTree as ET

import httpcore

from .common import Refused, digest, identity, now, require
from .project_tools import run_test


def public_url(value):
    require(type(value) is str and 0<len(value)<=3000 and all(ord(c)>32 and ord(c)!=127 for c in value),'Use a bounded public URL without control characters')
    p=urlsplit(value)
    try: port=p.port
    except ValueError: raise Refused('Invalid research URL port') from None
    require(p.scheme in {'http','https'} and p.hostname and not p.username and not p.password and '\\' not in value,'Research accepts public HTTP(S) URLs without credentials')
    require(port in {None,80 if p.scheme=='http' else 443},'Research uses the standard HTTP(S) port')
    require(not p.hostname.endswith(('.localhost','.local','.internal')) and p.hostname not in {'localhost','metadata.google.internal'},'Private hosts are unavailable to public research')
    try: address=ipaddress.ip_address(p.hostname)
    except ValueError: address=None
    require(address is None or (address.is_global and not address.is_multicast and not getattr(address,'ipv4_mapped',None)),'Private, mapped or special addresses are unavailable to research')
    try: parameters=parse_qsl(p.query.replace(';','&'),keep_blank_values=True,max_num_fields=128)
    except ValueError: raise Refused('Too many research URL parameters') from None
    for name,_ in parameters:
        # Servers may decode names more than once. Inspect bounded decoding and
        # refuse unresolved escapes instead of sending an obscured credential key.
        for _ in range(4):
            decoded=unquote_plus(name)
            if decoded==name: break
            name=decoded
        require(not re.search(r'%[0-9a-f]{2}',name,re.I),'Research URL parameter encoding is too deeply nested')
        normalized=re.sub(r'[^a-z0-9]','',name.lower())
        require(not re.search(r'(?:token|apikey|password|secret|authorization|credential|signature)',normalized),
            'Do not put credentials in research URLs')
    return p


class PublicBackend(httpcore.AsyncNetworkBackend):
    """Resolve once, validate every address, then connect to that numeric address.

    HTTPCore still uses the original hostname for Host and TLS verification.
    No second hostname lookup can redirect the socket into a private network.
    """
    def __init__(self,audit=None,delegate=None):
        self.audit=audit
        self.delegate=delegate or httpcore.AnyIOBackend()

    async def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
        require(port in {80,443} and local_address is None,'Invalid public research socket scope')
        async with asyncio.timeout(min(timeout or 10,10)):
            rows=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM)
        addresses=list(dict.fromkeys(row[4][0] for row in rows))
        require(0<len(addresses)<=32,'Unexpected research DNS answer')
        require(all(ipaddress.ip_address(a).is_global and not ipaddress.ip_address(a).is_multicast and not getattr(ipaddress.ip_address(a),'ipv4_mapped',None) for a in addresses),'Research DNS resolved to a private or special address')
        if self.audit: self.audit.observe('research.dns.checked',{'host':host,'port':port,'addresses':addresses})
        # A connection failure is returned to the work loop, with no effect retry.
        return await self.delegate.connect_tcp(addresses[0],port,timeout=timeout,socket_options=socket_options)

    async def connect_unix_socket(self,*args,**kwargs):
        raise Refused('Public research cannot open local sockets')

    async def sleep(self,seconds):
        await asyncio.sleep(seconds)


class PageText(HTMLParser):
    SKIP={'script','style','nav','footer','svg','noscript','form'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[];self.links=[];self.skip=[];self.in_title=False;self.title=[]
    def handle_starttag(self,tag,attrs):
        if tag in self.SKIP: self.skip.append(tag)
        if tag=='title': self.in_title=True
        if not self.skip:
            if tag in {'p','div','h1','h2','h3','li','pre','br','tr','section','article'}: self.parts.append('\n')
            if tag=='a':
                href=dict(attrs).get('href')
                if href and len(self.links)<100: self.links.append(href)
    def handle_endtag(self,tag):
        if self.skip and tag==self.skip[-1]: self.skip.pop()
        if tag=='title': self.in_title=False
        if not self.skip and tag in {'p','div','li','pre','h1','h2','h3'}: self.parts.append('\n')
    def handle_data(self,data):
        if self.in_title: self.title.append(data)
        if not self.skip: self.parts.append(data)
    def value(self,url):
        text=re.sub(r'[ \t\r\f\v]+',' ',''.join(self.parts))
        text=re.sub(r'\n\s*\n+','\n\n',text).strip()
        links=[]
        for link in self.links:
            target=urljoin(url,link)
            try: public_url(target)
            except Refused: continue
            if target not in links: links.append(target)
        return {'title':' '.join(''.join(self.title).split())[:300], 'text':text[:32000], 'truncated':len(text)>32000,'links':links[:40]}


class Research:
    def __init__(self,app,backend=None):
        self.app,self.db,self.audit=app,app.db,app.audit
        self.pool=httpcore.AsyncConnectionPool(network_backend=backend or PublicBackend(self.audit),max_connections=2,retries=0)

    async def close(self):
        await self.pool.aclose()

    async def request(self,url, *, limit=2000000):
        try:
            return await self._request(url,limit=limit)
        except BaseException as exc:
            self.audit.observe('research.http.interrupted' if isinstance(exc,asyncio.CancelledError) else 'research.http.failed',
                {'url_sha256':digest(url),'error_type':type(exc).__name__,'completed_response':False})
            raise

    async def _request(self,url, *, limit=2000000):
        route=[]
        for _ in range(4):
            parsed=public_url(url)
            self.audit.observe('research.http.requested',{'url':url,'method':'GET','credential_values':'none','route_index':len(route)})
            async with asyncio.timeout(25):
                async with self.pool.stream('GET',url,headers={'User-Agent':'MonkeyResearch/0.7 (public text research)',
                    'Accept':'text/html,application/xhtml+xml,application/xml,text/plain,application/json,application/pdf',
                    'Accept-Encoding':'identity'},extensions={'timeout':{'connect':10,'read':15,'write':10,'pool':5}}) as response:
                    headers={k.decode().lower():v.decode(errors='replace') for k,v in response.headers}
                    if response.status in {301,302,303,307,308}:
                        target=urljoin(url,headers.get('location',''))
                        require(target!=url and not (parsed.scheme=='https' and urlsplit(target).scheme!='https'),'Invalid or downgraded research redirect')
                        route.append({'url':url,'status':response.status,'to':target})
                        url=target
                        continue
                    require(response.status==200,'Public research returned HTTP '+str(response.status)+'; no authenticated or challenged content was accessed')
                    require(headers.get('content-encoding','identity').lower() in {'','identity'},'Research server sent unsupported compressed content')
                    raw=bytearray()
                    async for chunk in response.aiter_stream():
                        require(len(raw)+len(chunk)<=limit,'Research response exceeds the bounded source size')
                        raw.extend(chunk)
            result={'url':url,'redirects':route,'content_type':headers.get('content-type',''),'raw_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'captured_at':now()}
            self.audit.observe('research.http.completed',result)
            return bytes(raw),result
        raise Refused('Too many research redirects')

    def save(self,jid,kind,result):
        record=self.app.execution.seal({'id':identity('source_'),'kind':kind,'at':now(),'job_id':jid,**result,
            'authority':'untrusted public source; no tool authority or completion claim'})
        self.app.execution.save(jid,kind,{},'Research source captured: '+str(result.get('url',result.get('query',''))),record)
        return record

    async def search(self,jid,query):
        require(type(query) is str and 0<len(query.strip())<=240 and '\n' not in query,'Use a short public search query')
        require(not re.search(r'(?:api[_-]?key|password|secret|authorization)\s*[:=]|\bBearer\s+|[A-Za-z0-9_+/=-]{60,}',query,re.I),'Keep credentials and raw payloads out of search queries')
        url='https://www.bing.com/search?'+urlencode({'q':query,'format':'rss'})
        raw,metadata=await self.request(url)
        require(b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper(),'Search XML declarations are unsupported')
        try: feed=ET.fromstring(raw)
        except ET.ParseError: raise Refused('Search provider did not return its expected public feed') from None
        items=[]
        for item in feed.findall('./channel/item')[:10]:
            target=item.findtext('link') or ''
            try: public_url(target)
            except Refused: continue
            items.append({'title':(item.findtext('title') or '')[:300],'url':target,'snippet':(item.findtext('description') or '')[:800]})
        require(items,'Search returned no usable source links; revise the query or supply a public URL')
        return self.save(jid,'research.search',{**metadata,'query':query,'provider':'Bing public RSS search','results':items,
            'note':'Search snippets are discovery evidence. Read the original sources before citing claims.'})

    async def fetch(self,jid,url):
        raw,metadata=await self.request(url,limit=4000000)
        kind=metadata['content_type'].split(';')[0].lower()
        if kind=='application/pdf' or raw.startswith(b'%PDF-'):
            executable=shutil.which('pdftotext')
            require(executable,'An installed pdftotext decoder is required to read this PDF')
            with tempfile.TemporaryDirectory(prefix='monkey-research-pdf-',dir='/private/tmp') as folder:
                root=Path(folder);(root/'source.pdf').write_bytes(raw)
                result=await run_test(root,[executable,'-f','1','-l','12','-layout','source.pdf','-'],root/'scratch',audit=self.audit)
                require(result['exit_code']==0,'PDF text extraction failed; no text was inferred')
            content={'title':urlsplit(metadata['url']).path.rsplit('/',1)[-1],'text':result['output'][:32000],
                'truncated':True,'links':[],'extraction':'Installed pdftotext; first 12 pages; may omit image-only content'}
        else:
            require(kind.startswith('text/') or kind in {'application/xhtml+xml','application/json','application/xml','application/rss+xml'},'Research reads text and PDF sources, not executable or binary downloads')
            text=raw.decode('utf-8',errors='replace')
            if kind in {'text/html','application/xhtml+xml'}:
                parser=PageText();parser.feed(text);content=parser.value(metadata['url'])
            else:
                content={'title':metadata['url'],'text':text[:32000],'truncated':len(text)>32000,'links':[]}
        require(content['text'].strip(),'This source has no extractable text; no contents were invented')
        return self.save(jid,'research.source',{**metadata,**content,'text_sha256':hashlib.sha256(content['text'].encode()).hexdigest()})

    def sources(self,jid):
        return [self.app.execution.check_seal(r) for r in self.db.records('artifacts',jid) if r.get('kind')=='research.source']
