"""Explicit HTTP operations exposed through the same approval gate as MCP tools.

No endpoint is inferred from a service name. OpenAPI imports are local JSON,
resolve only local references, and reject unsupported parameter encodings.
"""
from contextlib import asynccontextmanager
import copy
import hashlib
import json
import re
from urllib.parse import quote, urlsplit

import httpx

from .common import Refused, digest, encoded, identity, require
from .security import bounded_json, header_fields, strict_json


class Packet:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **_):
        return self.value


def endpoint(value):
    require(type(value) is str, 'Select an explicit API base URL')
    require(all(32 < ord(c) < 127 for c in value) and '\\' not in value, 'Invalid API URL characters')
    url = urlsplit(value)
    require(url.hostname and not url.username and not url.password and not url.query and not url.fragment and
            (url.scheme == 'https' or url.scheme == 'http' and url.hostname in {'127.0.0.1', '::1'}),
            'API endpoints require HTTPS; loopback HTTP is allowed for local fixtures')
    operation_path(url.path or '/')
    require(url.port is None or 0 < url.port <= 65535, 'Invalid API port')
    return value.rstrip('/')


def operation_path(value):
    require(type(value) is str and value.startswith('/') and not value.startswith('//') and
            not any(part in value for part in ('?', '#', '\\', '%')) and
            not any(part in {'.', '..'} for part in value.split('/')) and
            all(32 < ord(c) < 127 for c in value), 'Use an API path relative to its fixed base URL')
    return value


def normalize(config):
    bounded_json(config)
    require(not set(config) - {'transport', 'base_url', 'headers', 'operations', 'openapi'}, 'Unsupported API configuration fields')
    config = copy.deepcopy(config)
    config['base_url'] = endpoint(config.get('base_url'))
    headers = config.get('headers', {})
    header_fields(headers)
    operations = config.get('operations', [])
    require(type(operations) is list and 0 < len(operations) <= 500, 'Configure between 1 and 500 explicit API operations')
    for op in operations:
        require(type(op) is dict and not set(op) - {'name', 'description', 'method', 'path', 'inputSchema', 'content_type','request_headers','error_key'}, 'Unsupported API operation fields')
        require(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', op.get('name','')), 'Give each API operation a stable exact name')
        require(op.get('method') in {'GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'}, 'Unsupported HTTP method')
        operation_path(op.get('path'))
        require(type(op.get('inputSchema')) is dict, 'Every API operation needs its exact inputSchema')
        require(not set(op['inputSchema'].get('properties',{})) - {'path','query','body','raw_body','headers'}, 'API arguments are path, query, body, raw_body or conditional headers')
        require(type(op.get('request_headers',[])) is list and set(op.get('request_headers',[])) <= {'If-Match','If-None-Match'}, 'Only explicit conditional request headers can be supplied by a tool')
        require(op.get('error_key') in {None,'errors','Fault'}, 'Unsupported service error envelope')
        require(op['inputSchema'].get('additionalProperties') is False, 'API argument schemas must set additionalProperties to false')
        op.setdefault('description', op['method']+' '+op['path'])
        op.setdefault('content_type', 'application/json')
        require(type(op['content_type']) is str and '\n' not in op['content_type'] and '\r' not in op['content_type'], 'Invalid API content type')
    require(len({op['name'] for op in operations}) == len(operations), 'Duplicate API operation identity')
    return config


def from_openapi(document, base_url, selected):
    """Compile a selected OpenAPI 3 subset; never fetch $refs or server URLs."""
    require(type(document) is dict and str(document.get('openapi','')).startswith('3.'), 'Import a local OpenAPI 3 JSON document')
    require(type(selected) is list and selected and all(type(x) is str for x in selected), 'Explicitly select OpenAPI operation IDs')

    def expand(value, trail=(), depth=0):
        require(depth < 35, 'OpenAPI reference nesting exceeds the supported bound')
        if isinstance(value,list):
            return [expand(v,trail,depth+1) for v in value]
        if not isinstance(value,dict):
            return value
        if '$ref' in value:
            ref = value['$ref']
            require(type(ref) is str and ref.startswith('#/') and ref not in trail, 'OpenAPI remote or recursive references require an explicit operation schema')
            node = document
            for key in ref[2:].split('/'):
                key = key.replace('~1','/').replace('~0','~')
                require(isinstance(node,dict) and key in node, 'Missing OpenAPI reference')
                node = node[key]
            require(len(value) == 1, 'OpenAPI reference siblings require an explicit operation schema')
            return expand(node,(*trail,ref),depth+1)
        require('nullable' not in value, 'Convert OpenAPI nullable to a JSON Schema union before import')
        return {k:expand(v,trail,depth+1) for k,v in value.items()}

    operations = []
    for path, item in document.get('paths',{}).items():
        require(isinstance(item,dict), 'Invalid OpenAPI path item')
        for method, raw in item.items():
            if method.lower() not in {'get','head','post','put','patch','delete','options'} or not isinstance(raw,dict) or raw.get('operationId') not in selected:
                continue
            props, required = {}, []
            parameters = [*item.get('parameters',[]),*raw.get('parameters',[])]
            for param in map(expand,parameters):
                location = param.get('in')
                require(location in {'path','query'}, 'Header/cookie parameters require a manually scoped API operation')
                schema = param.get('schema',{})
                require(schema.get('type') in {'string','integer','number','boolean'}, 'Only scalar OpenAPI path/query encoding is supported')
                require(param.get('style', 'simple' if location=='path' else 'form') == ('simple' if location=='path' else 'form'), 'Unsupported OpenAPI parameter style')
                group = props.setdefault(location,{'type':'object','properties':{},'required':[],'additionalProperties':False})
                name = param['name']
                require(name not in group['properties'], 'Overridden OpenAPI parameters require an explicit operation schema')
                group['properties'][name] = schema
                if param.get('required') or location=='path':
                    group['required'].append(name)
                    if location not in required:
                        required.append(location)
            if raw.get('requestBody'):
                body = expand(raw['requestBody'])
                require('application/json' in body.get('content',{}), 'OpenAPI import supports JSON bodies; configure raw_body explicitly for other media')
                props['body'] = body['content']['application/json'].get('schema',{})
                if body.get('required'):
                    required.append('body')
            operations.append({'name':raw['operationId'],'method':method.upper(),'path':path,
                'description':str(raw.get('summary',raw.get('description',raw['operationId'])))[:4000],
                'inputSchema':{'type':'object','properties':props,'required':required,'additionalProperties':False}})
    require({o['name'] for o in operations} == set(selected), 'A selected OpenAPI operation ID is missing or unsupported')
    return normalize({'transport':'api','base_url':base_url,'operations':operations})


class APIClient:
    def __init__(self, config, audit=None):
        self.config = normalize(config)
        self.audit = audit

    async def list_tools(self, **_):
        return Packet({'tools':[{'name':op['name'],'description':op['description'],'inputSchema':op['inputSchema']} for op in self.config['operations']]})

    def request(self, name, arguments):
        op = next(op for op in self.config['operations'] if op['name']==name)
        require(not set(arguments)-{'path','query','body','raw_body','headers'}, 'Unrecognized HTTP argument')
        params = arguments.get('path',{})
        path = op['path']
        require(type(params) is dict and set(params) == set(re.findall(r'\{([^{}]+)\}',path)), 'Exact API path parameters required')
        for key,value in params.items():
            require(type(value) in {str,int,float,bool} and str(value) not in {'','.','..'} and
                not any(c in str(value) for c in '/\\%?#') and all(ord(c)>=32 and ord(c)!=127 for c in str(value)), 'Invalid API path parameter; encoded path separators are unavailable')
            path = path.replace('{'+key+'}',quote(str(value),safe=''))
        require('{' not in path and '}' not in path, 'Invalid API path template')
        query = arguments.get('query',{})
        require(type(query) is dict and all(type(v) in {str,int,float,bool} for v in query.values()), 'API query parameters must be scalar')
        require(not ('body' in arguments and 'raw_body' in arguments), 'Choose JSON body or raw_body')
        body = encoded(arguments['body']) if 'body' in arguments else arguments.get('raw_body','').encode()
        require(len(body)<=45000,'API body exceeds the approved request bound')
        headers = dict(self.config.get('headers',{}))
        conditional = arguments.get('headers',{})
        require(type(conditional) is dict and set(conditional)<=set(op.get('request_headers',[])) and
            all(type(v) is str and not any(c in v for c in '\r\n\x00') for v in conditional.values()),'Unapproved HTTP request header')
        headers.update(conditional)
        headers = {k:v for k,v in headers.items() if k.lower() not in {'content-type','accept-encoding'}}
        headers['Accept-Encoding'] = 'identity'
        if 'body' in arguments or 'raw_body' in arguments:
            headers['Content-Type'] = op['content_type']
        return op, path, query, headers, body

    async def call_tool(self, name, arguments, **_):
        op,path,query,headers,body=self.request(name,arguments)
        operation_id=identity('http_')
        if self.audit: self.audit.observe('http.requested',{'operation_id':operation_id,'method':op['method'],'url':self.config['base_url']+path,
            'arguments_hash':digest(arguments),'body_sha256':hashlib.sha256(body).hexdigest(),'header_names':sorted(headers),'credential_values':'excluded'})
        try:
            result=await self._call_tool(name,arguments)
        except BaseException as exc:
            if self.audit: self.audit.observe('http.failed',{'operation_id':operation_id,'error_type':type(exc).__name__,'remote_outcome':'unresolved if a write may have been sent'})
            raise
        if self.audit: self.audit.observe('http.completed',{'operation_id':operation_id,'response_hash':digest(result.value),'http_status':result.value['structuredContent']['http_status']})
        return result

    async def _call_tool(self, name, arguments):
        op, path, query, headers, body = self.request(name, arguments)
        # Exactly one request. No redirect, retry, ambient proxy or ambient auth.
        async with httpx.AsyncClient(timeout=40,follow_redirects=False,trust_env=False) as client:
            async with client.stream(op['method'],self.config['base_url']+path,params=query,headers=headers,content=body) as response:
                require(response.headers.get('content-encoding','identity').lower() in {'','identity'}, 'Compressed API response refused under the memory bound; inspect the outcome')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    require(len(data)<=750000,'API response exceeds retained bound; inspect the service outcome')
                require(not response.is_redirect,'API redirect refused; inspect the outcome before a newly approved request')
                raw = data.decode('utf-8',errors='replace')
                try:
                    payload = strict_json(raw, limit=750000)
                except Refused:
                    require(not raw.lstrip().startswith(('{','[')), 'Ambiguous or excessive service JSON; outcome needs inspection')
                    payload = raw
                return Packet({'content':[{'type':'text','text':raw}], 'structuredContent':{'http_status':response.status_code,'body':payload,
                    'response_headers':{k:response.headers[k] for k in ('etag','last-modified','retry-after') if k in response.headers}},
                    'isError':response.status_code>=400 or bool(op.get('error_key') and isinstance(payload,dict) and payload.get(op['error_key']))})


@asynccontextmanager
async def api_client(config, audit=None):
    yield APIClient(config,audit=audit)
