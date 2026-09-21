"""Monkey-owned service adapters and guided setup; no user-authored config required."""
import re
from urllib.parse import urlsplit

from .api_tools import endpoint, normalize
from .common import identity, now, require

TEXT = {'type':'string','minLength':1,'maxLength':6000}
ID = {'type':'string','minLength':1,'maxLength':256}
DATE = {'type':'string','pattern':r'^\d{4}-\d{2}-\d{2}$'}
BOOL = {'type':'boolean'}


def obj(properties=None, required=None, **extra):
    return {'type':'object','properties':properties or {},'required':list(properties or {}) if required is None else required,
        'additionalProperties':False,**extra}


def operation(name, method, path, *, body=None, query=None, headers=None, description=None, error_key=None):
    names = re.findall(r'\{([^{}]+)\}',path)
    props = {'path':obj({key:ID for key in names})} if names else {}
    if body is not None:
        props['body'] = body
    if query is not None:
        props['query'] = query
    if headers:
        props['headers'] = obj({key:TEXT for key in headers})
    result = {'name':name,'method':method,'path':path,'inputSchema':obj(props),
        'description':description or name.replace('_',' ')}
    if headers:
        result['request_headers'] = headers
    if error_key:
        result['error_key'] = error_key
    return result


def graphql(name, query, variables=None, description=None):
    props = {'query':{'type':'string','const':query}}
    if variables is not None:
        props['variables'] = variables
    return operation(name,'POST','/v2',body=obj(props),description=description,error_key='errors')


PROVIDERS = {
    'asana':('Asana','Tasks, projects and comments','https://developers.asana.com/reference/createtask'),
    'teams':('Teams / Planner','Channels, messages and Planner tasks','https://learn.microsoft.com/en-us/graph/api/planner-post-tasks?view=graph-rest-1.0'),
    'monday':('monday.com','Boards, items and updates','https://developer.monday.com/api-reference/reference/items'),
    'salesforce':('Salesforce','Task records and object metadata','https://developer.salesforce.com/docs/platform/api-rest/guide/dome-sobject-create.html'),
    'quickbooks':('QuickBooks Online','Company information, customers and invoices','https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/invoice'),
    'mcp':('Another MCP server','Connect an existing MCP endpoint','https://modelcontextprotocol.io'),
    'api':('Another HTTP API','Define an operation inside Monkey',''),
}
ALIASES = {'monday.com':'monday','planner':'teams','quickbooks-online':'quickbooks'}


def native_config(provider, secret, options):
    require(type(secret) is str and 0<len(secret.strip())<=16000 and not any(c in secret for c in '\r\n\x00'),
        'Enter the service access token in the hidden credential field')
    headers = {'Authorization':secret if provider=='monday' else 'Bearer '+secret,'Accept':'application/json'}
    if provider=='asana':
        base = 'https://app.asana.com/api/1.0'
        fields = {'name':TEXT,'notes':TEXT,'completed':BOOL,'start_on':DATE,'due_on':DATE}
        ops = [operation('list_workspaces','GET','/workspaces'),operation('get_user','GET','/users/me'),
            operation('list_projects','GET','/workspaces/{workspace_gid}/projects'),
            operation('list_project_tasks','GET','/projects/{project_gid}/tasks'),
            operation('get_task','GET','/tasks/{task_gid}'),
            operation('create_task','POST','/tasks',body=obj({'data':obj({**fields,'workspace':ID,
                'projects':{'type':'array','items':ID,'maxItems':10}},['name','workspace'])})),
            operation('update_task','PUT','/tasks/{task_gid}',body=obj({'data':obj(fields,[],minProperties=1)})),
            operation('add_comment','POST','/tasks/{task_gid}/stories',body=obj({'data':obj({'text':TEXT})}))]
    elif provider=='teams':
        base = 'https://graph.microsoft.com/v1.0'
        fields = {'title':TEXT,'bucketId':ID,'startDateTime':TEXT,'dueDateTime':TEXT,
            'percentComplete':{'type':'integer','minimum':0,'maximum':100}}
        ops = [operation('list_teams','GET','/me/joinedTeams'),operation('list_channels','GET','/teams/{team_id}/channels'),
            operation('list_plans','GET','/groups/{group_id}/planner/plans'),
            operation('list_plan_tasks','GET','/planner/plans/{plan_id}/tasks'),
            operation('get_task','GET','/planner/tasks/{task_id}'),
            operation('create_task','POST','/planner/tasks',body=obj({**fields,'planId':ID},['planId','title'])),
            operation('update_task','PATCH','/planner/tasks/{task_id}',body=obj(fields,[],minProperties=1),headers=['If-Match']),
            operation('get_message','GET','/teams/{team_id}/channels/{channel_id}/messages/{message_id}'),
            operation('send_message','POST','/teams/{team_id}/channels/{channel_id}/messages',
                body=obj({'body':obj({'contentType':{'type':'string','const':'text'},'content':TEXT})}),
                description='Send the exact reviewed channel message. Requires delegated ChannelMessage.Send; not an application migration operation.')]
    elif provider=='monday':
        base = 'https://api.monday.com'
        headers['API-Version'] = '2026-07'
        ids = obj({'ids':{'type':'array','items':ID,'minItems':1,'maxItems':100}})
        ops = [graphql('get_user','query { me { id name } }'),
            graphql('list_boards','query { boards(limit: 100) { id name } }'),
            graphql('get_board','query ($ids: [ID!]!) { boards(ids: $ids) { id name groups { id title } columns { id title type } items_page(limit: 100) { cursor items { id name } } } }',ids),
            graphql('get_item','query ($ids: [ID!]!) { items(ids: $ids) { id name board { id } column_values { id text value } } }',ids),
            graphql('create_item','mutation ($board: ID!, $group: String!, $name: String!) { create_item(board_id: $board, group_id: $group, item_name: $name) { id name } }',obj({'board':ID,'group':ID,'name':TEXT})),
            graphql('update_columns','mutation ($board: ID!, $item: ID!, $values: JSON!) { change_multiple_column_values(board_id: $board, item_id: $item, column_values: $values) { id name } }',obj({'board':ID,'item':ID,'values':TEXT})),
            graphql('add_update','mutation ($item: ID!, $body: String!) { create_update(item_id: $item, body: $body) { id body } }',obj({'item':ID,'body':TEXT}))]
    elif provider=='salesforce':
        base = endpoint(options.get('endpoint'))
        host = urlsplit(base)
        require(not host.path and host.scheme=='https' and host.hostname.endswith(('.salesforce.com','.force.com')), 'Use your exact HTTPS Salesforce instance origin')
        version = options.get('api_version') or 'v64.0'
        require(re.fullmatch(r'v[0-9]{2,3}\.0',version),'Use an explicit Salesforce REST version, such as v64.0')
        base += '/services/data/'+version
        fields = {'Subject':TEXT,'Description':TEXT,'ActivityDate':DATE,'Status':TEXT,'Priority':TEXT,'OwnerId':ID,'WhoId':ID,'WhatId':ID}
        ops = [operation('describe_task','GET','/sobjects/Task/describe'),operation('get_task','GET','/sobjects/Task/{task_id}'),
            operation('create_task','POST','/sobjects/Task',body=obj(fields,['Subject'])),
            operation('update_task','PATCH','/sobjects/Task/{task_id}',body=obj(fields,[],minProperties=1))]
    elif provider=='quickbooks':
        company = options.get('company_id','')
        require(re.fullmatch(r'[0-9]{1,30}',company),'Enter the exact QuickBooks company / realm ID')
        environment = options.get('environment') or 'sandbox'
        require(environment in {'sandbox','production'},'Choose sandbox or production explicitly')
        base = 'https://'+('sandbox-' if environment=='sandbox' else '')+'quickbooks.api.intuit.com/v3/company/'+company
        ref = obj({'value':ID})
        amount = {'type':'number','minimum':0}
        line = obj({'Amount':amount,'DetailType':{'type':'string','const':'SalesItemLineDetail'},
            'SalesItemLineDetail':obj({'ItemRef':ref,'Qty':amount,'UnitPrice':amount},['ItemRef'])})
        ops = [operation('company_info','GET','/companyinfo/'+company,error_key='Fault'),
            operation('get_customer','GET','/customer/{customer_id}',error_key='Fault'),
            operation('get_invoice','GET','/invoice/{invoice_id}',error_key='Fault'),
            operation('create_invoice','POST','/invoice',body=obj({'CustomerRef':ref,
                'Line':{'type':'array','items':line,'minItems':1,'maxItems':50},'DueDate':DATE,'TxnDate':DATE,'DocNumber':TEXT},['CustomerRef','Line']),error_key='Fault')]
    else:
        require(False,'Select a built-in service')
    return normalize({'transport':'api','base_url':base,'headers':headers,'operations':ops})


class Services:
    def __init__(self, app):
        self.app = app

    def list(self):
        connections = self.app.connectors.connections()
        return {'services':[{'id':key,'name':value[0],'description':value[1],'documentation':value[2],
            'state':'configured · access unchecked' if key in connections else 'ready to set up'} for key,value in PROVIDERS.items()],
            'message':'Monkey builds and owns these adapters. /connect SERVICE starts setup; no configuration file is needed.'}

    def form(self, provider=None):
        if provider is None:
            return {'form':{'operation':'service-select','target':None,'expected_version':None,'index':0,'values':{},
                'fields':[('service','Choose asana / teams / monday / salesforce / quickbooks / mcp / api','')]}}
        provider = ALIASES.get(provider,provider)
        require(provider in PROVIDERS,'Choose one of: '+', '.join(PROVIDERS))
        fields = []
        if provider=='salesforce':
            fields += [('endpoint','Your Salesforce instance · https://YOUR-ORG.my.salesforce.com',''),('api_version','Salesforce REST version','v64.0')]
        if provider=='quickbooks':
            fields += [('company_id','QuickBooks company / realm ID',''),('environment','QuickBooks environment · sandbox or production','sandbox')]
        if provider in {'mcp','api'}:
            fields += [('name','Connection name · lowercase letters, numbers and hyphens',''),('endpoint','Exact '+('MCP endpoint' if provider=='mcp' else 'API base URL'),'')]
        if provider=='api':
            fields += [('operation_name','Name this API operation',''),('method','HTTP method','GET'),('operation_path','Path relative to the API base URL','/'),
                ('body_schema','JSON body schema · enter {} for no body','{}')]
        fields += [('secret','Access token · hidden, optional for an unauthenticated custom endpoint' if provider in {'mcp','api'} else PROVIDERS[provider][0]+' access token · hidden','')]
        return {'form':{'operation':'service-save','target':None,'expected_version':None,'index':0,'values':{'service':provider},
            'fields':fields,'secret_fields':['secret'],'optional_fields':['secret'] if provider in {'mcp','api'} else []},
            'message':'Monkey creates the adapter and stores credentials privately. This does not send a task or message.'}

    async def save(self, service, secret, options):
        require(service in PROVIDERS,'Unknown service')
        name = options.get('name') or service
        if service in {'mcp','api'}:
            require(type(secret) is str and len(secret)<=16000 and not any(c in secret for c in '\r\n\x00'),'Invalid access token')
            headers = {'Authorization':'Bearer '+secret} if secret else {}
            if service=='mcp':
                config = {'transport':'streamable-http','url':endpoint(options.get('endpoint')),'headers':headers}
            else:
                import json
                body = json.loads(options.get('body_schema') or '{}')
                require(type(body) is dict,'Enter a JSON Schema object for the body')
                op = operation(options.get('operation_name'),(options.get('method') or 'GET').upper(),options.get('operation_path') or '/',body=body or None)
                config = normalize({'transport':'api','base_url':endpoint(options.get('endpoint')),'headers':headers,'operations':[op]})
        else:
            config = native_config(service,secret,options)
        result = await self.app.connectors.connect_config(name,config)
        return {**result,'message':PROVIDERS[service][0]+' adapter saved by Monkey. '+str(len(result['tools']))+
            ' tools available. Account access is checked when you run an explicitly reviewed request; no task was sent.',
            'access_verified':False,'managed_by':'Monkey'}

    def disconnect(self, name):
        require(name in self.app.connectors.connections(),'Unknown connection')
        require(not self.app.execution.active,'Wait for active work to settle before disconnecting its service')
        row = self.app.execution.seal({'id':identity('disconnected_'),'kind':'connector.disconnected','name':name,'at':now()})
        self.app.db.global_event('connector.disconnected',{'message':name+' disconnected. Existing receipts are retained; pending requests need a fresh connection.'},
            self.app.command('disconnect'),record=('artifacts',row['id'],row))
        return {'message':name+' disconnected; completed work evidence is retained.'}
