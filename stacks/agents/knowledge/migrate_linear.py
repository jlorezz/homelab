#!/usr/bin/env python3
"""One-time, resumable Linear -> GitHub migration from an exported connector snapshot.
Source records are preserved. This script never changes Linear, application settings,
branch protections, checks, or deployments. Imported comments retain attribution in
issue bodies rather than impersonating their authors as new GitHub comments.
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import time


def gh_api(route, payload=None, method=None):
    cmd = ['gh', 'api', route]
    if method: cmd += ['--method', method]
    if payload is not None: cmd += ['--input', '-']
    result = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None, capture_output=True, text=True, timeout=90)
    if result.returncode: raise RuntimeError('GitHub request failed: ' + route + ': ' + result.stderr[:500])
    value = json.loads(result.stdout) if result.stdout.strip() else {}
    if isinstance(value, dict) and value.get('errors'): raise RuntimeError(str(value['errors']))
    return value


def graphql(query, variables):
    return gh_api('graphql', {'query': query, 'variables': variables})['data']


def pages(route):
    result = subprocess.run(['gh', 'api', '--paginate', '--slurp', route], capture_output=True, text=True, check=True, timeout=90)
    return [item for page in json.loads(result.stdout) for item in page]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def text(value):
    # Preserve old mentions without notifying unrelated GitHub accounts.
    return re.sub(r'(?<![\w/])@([\w-]+)', '@\u200b\\1', value or '')


def render(detail, comments, mapping, repo):
    ident = detail['id']
    body = f'<!-- linear-migration:{detail["uuid"]} -->\n\n'
    body += f'Imported from [{ident}]({detail["url"]}). GitHub is now the work tracker; the Linear record is retained for provenance.\n\n'
    body += '| Original metadata | Value |\n| --- | --- |\n'
    for label, value in [('State',detail.get('status')),('Priority',(detail.get('priority') or {}).get('name')),('Project',detail.get('project')),('Assignee',detail.get('assignee')),('Created',detail.get('createdAt')),('Due',detail.get('dueDate')),('Completed',detail.get('completedAt')),('Archived',detail.get('archivedAt'))]:
        if value: body += f'| {label} | {str(value).replace("|", "/")} |\n'
    body += '\n## Original description\n\n' + text(detail.get('description')) + '\n'
    attachments = detail.get('attachments', [])
    if attachments:
        body += '\n## Linked evidence\n\n'
        for attachment in attachments:
            url = attachment.get('url') or attachment.get('assetUrl')
            if url: body += '- [' + (attachment.get('title') or attachment.get('label') or 'Attachment').replace(']', '') + '](' + url + ')\n'
    if comments:
        body += '\n## Imported discussion\n\nThese are historical Linear comments, with original author and timestamps.\n'
        for comment in sorted(comments, key=lambda c:c['createdAt']):
            author = (comment.get('author') or {}).get('name', 'Unknown author')
            body += '\n<details>\n<summary>' + text(author) + ' · ' + comment['createdAt'] + '</summary>\n\n'
            if comment.get('quotedText'): body += 'Original inline anchor:\n\n> ' + comment['quotedText'].replace('\n','\n> ') + '\n\n'
            body += text(comment.get('body')) + '\n\n</details>\n'
    relations = detail.get('relations', {})
    links = []
    if detail.get('parentId'): links.append(('Parent', detail['parentId']))
    for key in ['blocks', 'blockedBy', 'relatedTo']:
        links.extend((key, r['id']) for r in relations.get(key, []))
    if relations.get('duplicateOf'): links.append(('Duplicate of', relations['duplicateOf']['id']))
    refs = set(re.findall(r'\bENG-\d+\b', detail.get('description','') + '\n' + '\n'.join(c.get('body','') for c in comments)))
    links.extend(('Referenced issue', r) for r in sorted(refs) if r != ident and r not in {v for _,v in links})
    if links:
        body += '\n## GitHub relationships\n\n'
        for kind, old in links:
            if old in mapping: body += f'- {kind}: [{old}]({mapping[old]["html_url"]})\n'
            else: body += f'- {kind}: {old} (source record not in this export)\n'
    if len(body) > 65000: raise ValueError('Issue body exceeds GitHub envelope: '+ident)
    return body


def project_fields(project_id):
    result = graphql('query($id:ID!){node(id:$id){... on ProjectV2{fields(first:50){nodes{... on ProjectV2Field{id name} ... on ProjectV2SingleSelectField{id name options{id name}}}} views(first:50){nodes{id name}}}}}', {'id':project_id})['node']
    return {f['name']:f for f in result['fields']['nodes']}, {v['name']:v for v in result['views']['nodes']}


def configure_project(project_id, repo, title):
    repo_id = gh_api('repos/'+repo)['node_id']
    graphql('mutation($p:ID!,$r:ID!){linkProjectV2ToRepository(input:{projectId:$p,repositoryId:$r}){repository{id}}}',{'p':project_id,'r':repo_id})
    readme = f'{title} work tracking lives in GitHub Issues and this project.\n\nUse an issue for scope, acceptance criteria and evidence; link the implementation PR. Current repository instructions govern implementation and merges. Approved durable lessons live in the NAS Brain.\n\nActive board: open work. History: completed/canceled issues. Milestones preserve migrated Linear workstreams; original comments and IDs are retained in each imported issue. Linear is a preserved migration source, not the ongoing tracker.'
    graphql('mutation($id:ID!,$readme:String!){updateProjectV2(input:{projectId:$id,shortDescription:"Work tracking; durable knowledge lives in the NAS Brain.",readme:$readme,public:false}){projectV2{id}}}', {'id':project_id,'readme':readme})
    fields, views = project_fields(project_id)
    # Use GitHub's first generated view as the useful landing board. Preserve
    # the separately created view as an all-work table rather than deleting it.
    if 'View 1' in views:
        first = views.pop('View 1')
        previous = views.pop('Active board', None)
        graphql('mutation($id:ID!){updateProjectV2View(input:{viewId:$id,name:"Active board",layout:BOARD_LAYOUT,filter:"is:open"}){projectV2View{id}}}', {'id': first['id']})
        views['Active board'] = first
        if previous:
            graphql('mutation($id:ID!){updateProjectV2View(input:{viewId:$id,name:"All work",layout:TABLE_LAYOUT,filter:""}){projectV2View{id}}}', {'id': previous['id']})
            views['All work'] = previous
    statuses = ['Backlog','Ready','In Progress','In Review','On Hold','Done','Canceled']
    colors = ['GRAY','BLUE','YELLOW','PURPLE','ORANGE','GREEN','RED']
    if [o['name'] for o in fields['Status']['options']] != statuses:
        graphql('mutation($id:ID!,$options:[ProjectV2SingleSelectFieldOptionInput!]){updateProjectV2Field(input:{fieldId:$id,singleSelectOptions:$options}){projectV2Field{... on ProjectV2SingleSelectField{id}}}}', {'id':fields['Status']['id'],'options':[{'name':n,'color':c,'description':n} for n,c in zip(statuses,colors)]})
    if 'Priority' not in fields:
        graphql('mutation($id:ID!,$options:[ProjectV2SingleSelectFieldOptionInput!]){createProjectV2Field(input:{projectId:$id,dataType:SINGLE_SELECT,name:"Priority",singleSelectOptions:$options}){projectV2Field{... on ProjectV2SingleSelectField{id}}}}', {'id':project_id,'options':[{'name':n,'color':c,'description':n} for n,c in [('Urgent','RED'),('High','ORANGE'),('Medium','YELLOW'),('Low','BLUE'),('None','GRAY')]]})
    for name,layout,filter_ in [('Active board','BOARD_LAYOUT','is:open'),('Backlog','TABLE_LAYOUT','is:open status:Backlog'),('History','TABLE_LAYOUT','is:closed')]:
        if name not in views:
            result = graphql('mutation($id:ID!,$name:String!,$layout:ProjectV2ViewLayout!){createProjectV2View(input:{projectId:$id,name:$name,layout:$layout}){projectV2View{id}}}',{'id':project_id,'name':name,'layout':layout})
            views[name]=result['createProjectV2View']['projectV2View']
        graphql('mutation($id:ID!,$filter:String!){updateProjectV2View(input:{viewId:$id,filter:$filter}){projectV2View{id}}}',{'id':views[name]['id'],'filter':filter_})
    return project_fields(project_id)[0]


def migrate(export, state_path, repo, project_id):
    data = json.loads(export.read_text())
    state = json.loads(state_path.read_text()) if state_path.exists() else {'repo':repo,'issues':{},'milestones':{},'items':{},'relations':[]}
    if state['repo'] != repo: raise ValueError('State repository mismatch')
    # Recover accepted creates before retrying any mutation; provenance UUID is stable.
    for item in pages(f'repos/{repo}/issues?state=all&per_page=100'):
        if item.get('pull_request'): continue
        marker = re.search(r'<!-- linear-migration:([0-9a-f-]+) -->', item.get('body') or '')
        if marker:
            source = next((i for i in data['issues'] if i['uuid'] == marker[1]), None)
            if source: state['issues'][source['id']] = {k:item[k] for k in ['id','node_id','number','html_url']}
    save(state_path,state)
    existing_labels = {l['name'].lower():l['name'] for l in pages(f'repos/{repo}/labels?per_page=100')}
    labels = sorted({l for issue in data['issues'] for l in issue.get('labels',[])} | {'migrated: linear','status: on-hold','status: canceled','status: in-review'})
    for label in labels:
        if label.lower() not in existing_labels:
            gh_api(f'repos/{repo}/labels',{'name':label,'color':'607D8B','description':'Preserved Linear classification' if not label.startswith('status:') else 'Migrated workflow state'})
            existing_labels[label.lower()] = label
    existing_milestones = {m['title']:m for m in pages(f'repos/{repo}/milestones?state=all&per_page=100')}
    for project in data['project_full'].values():
        name = project['name']
        if name not in existing_milestones:
            description = f'Imported from {project["url"]}\nOriginal status: {project["status"]["name"]}\n\n' + project.get('description','')
            payload={'title':name,'description':description,'state':'closed' if project['status']['type'] in {'completed','canceled'} else 'open'}
            existing_milestones[name]=gh_api(f'repos/{repo}/milestones',payload)
        state['milestones'][name]=existing_milestones[name]['number']
    fields=configure_project(project_id,repo,'Dealpipe')
    save(state_path,state)
    issues=sorted(data['issues'],key=lambda i:int(i['id'].split('-')[1]))
    for position,summary in enumerate(issues,1):
        ident=summary['id']; full=data['full'][ident]; detail=full['detail']
        if ident not in state['issues']:
            labels=[existing_labels[l.lower()] for l in detail.get('labels',[])]+['migrated: linear']
            if detail.get('status') in {'On Hold','Canceled','In Review'}:labels.append({'On Hold':'status: on-hold','Canceled':'status: canceled','In Review':'status: in-review'}[detail['status']])
            payload={'title':ident+': '+detail['title'],'body':render(detail,full['comments']['comments'],state['issues'],repo),'labels':labels,'milestone':state['milestones'][detail['project']]}
            if detail.get('assignee')=='Florian Bress':payload['assignees']=['jlorezz']
            item=gh_api(f'repos/{repo}/issues',payload)
            state['issues'][ident]={k:item[k] for k in ['id','node_id','number','html_url']};save(state_path,state)
            # Stay below GitHub's secondary content-creation admission ceiling.
            time.sleep(1.1)
        if position%25==0:print(json.dumps({'created_or_recovered':position,'total':len(issues)}),flush=True)
    # All IDs now exist: publish cross-links and exact closed states in one update.
    for position,summary in enumerate(issues,1):
        ident=summary['id'];full=data['full'][ident];detail=full['detail'];item=state['issues'][ident]
        if not item.get('finalized'):
            payload={'body':render(detail,full['comments']['comments'],state['issues'],repo)}
            if detail.get('statusType') in {'completed','canceled'}:payload.update(state='closed',state_reason='completed' if detail['statusType']=='completed' else 'not_planned')
            gh_api(f'repos/{repo}/issues/{item["number"]}',payload,method='PATCH')
            item['finalized']=True;save(state_path,state)
        if ident not in state['items']:
            result=graphql('mutation($p:ID!,$i:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$i}){item{id}}}',{'p':project_id,'i':item['node_id']})
            state['items'][ident]=result['addProjectV2ItemById']['item']['id'];save(state_path,state)
        if not item.get('fields_set'):
            status={'Todo':'Ready','In Review':'In Review','On Hold':'On Hold','Done':'Done','Canceled':'Canceled','Backlog':'Backlog'}.get(detail['status'],'In Progress')
            priority=(detail.get('priority') or {}).get('name','None')
            mutations=[];variables={'p':project_id,'i':state['items'][ident]}
            for n,(name,value) in enumerate([('Status',status),('Priority',priority)]):
                field=fields[name];option=next(o['id'] for o in field['options'] if o['name']==value)
                mutations.append(f'f{n}:updateProjectV2ItemFieldValue(input:{{projectId:$p,itemId:$i,fieldId:"{field["id"]}",value:{{singleSelectOptionId:"{option}"}}}}){{projectV2Item{{id}}}}')
            graphql('mutation($p:ID!,$i:ID!){'+''.join(mutations)+'}',variables)
            item['fields_set']=True;save(state_path,state)
        parent=detail.get('parentId')
        if parent and parent in state['issues'] and ident not in state['relations']:
            result=graphql('mutation($p:ID!,$c:ID!){addSubIssue(input:{issueId:$p,subIssueId:$c}){issue{id}}}',{'p':state['issues'][parent]['node_id'],'c':item['node_id']})
            state['relations'].append(ident);save(state_path,state)
        if position%25==0:print(json.dumps({'finalized':position,'total':len(issues)}),flush=True)
    state['completed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save(state_path,state)
    print(json.dumps({'migrated':len(state['issues']),'project_items':len(state['items']),'subissues':len(state['relations']),'milestones':len(state['milestones'])}),flush=True)


def sync_due_dates(export, state_path, project_id):
    data = json.loads(Path(export).read_text())
    state = json.loads(Path(state_path).read_text())
    fields, views = project_fields(project_id)
    if 'Target date' not in fields:
        graphql('mutation($id:ID!){createProjectV2Field(input:{projectId:$id,dataType:DATE,name:"Target date"}){projectV2Field{... on ProjectV2Field{id}}}}', {'id':project_id})
        fields, views = project_fields(project_id)
    candidates = [(key, value['detail']['dueDate']) for key,value in data['full'].items() if value['detail'].get('dueDate')]
    for start in range(0, len(candidates), 15):
        mutations = []
        for n,(key,date) in enumerate(candidates[start:start+15]):
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date): raise ValueError('Unexpected source date')
            mutations.append(f'd{n}:updateProjectV2ItemFieldValue(input:{{projectId:"{project_id}",itemId:"{state["items"][key]}",fieldId:"{fields["Target date"]["id"]}",value:{{date:"{date}"}}}}){{projectV2Item{{id}}}}')
        graphql('mutation{'+''.join(mutations)+'}', {})
    visible=[fields[n]['id'] for n in ['Title','Status','Priority','Assignees','Milestone','Phase','Target date','Labels'] if n in fields]
    for view in views.values():
        graphql('mutation($id:ID!,$fields:[ID!]){updateProjectV2View(input:{viewId:$id,configuration:{visibleFieldIds:$fields}}){projectV2View{id}}}', {'id':view['id'],'fields':visible})
    return len(candidates)


def sync_milestone_definitions(export, state_path):
    data=json.loads(Path(export).read_text());state=json.loads(Path(state_path).read_text())
    if state.get('phase_definitions_migrated'):return state['phase_definitions_migrated']
    count=0
    for project in data['project_full'].values():
        description=f'Imported from {project["url"]}\nOriginal status: {project["status"]["name"]}\n\n'+text(project.get('description',''))
        for phase in project.get('milestones',[]):
            count+=1
            description+='\n\n### '+phase['name']+'\n\n'
            if phase.get('targetDate'):description+='Original target date: '+str(phase['targetDate'])+'\n\n'
            if phase.get('progress'):description+='Original progress: '+str(phase['progress'])+'\n\n'
            description+=text(phase.get('description',''))
        gh_api(f'repos/{state["repo"]}/milestones/{state["milestones"][project["name"]]}',{'description':description},method='PATCH')
    state['phase_definitions_migrated']=count;save(Path(state_path),state)
    return count


def sync_phases_dependencies(export, state_path, project_id):
    data = json.loads(Path(export).read_text())
    state = json.loads(Path(state_path).read_text())
    if not state.get('completed_at'): raise ValueError('Finish issue migration first')
    fields, _ = project_fields(project_id)
    if 'Phase' not in fields:
        graphql('mutation($id:ID!){createProjectV2Field(input:{projectId:$id,dataType:TEXT,name:"Phase"}){projectV2Field{... on ProjectV2Field{id}}}}', {'id':project_id})
        fields, _ = project_fields(project_id)
    completed = set(state.get('phases', []))
    phases = [(key, value['detail']['projectMilestone']['name']) for key,value in data['full'].items() if value['detail'].get('projectMilestone') and key not in completed]
    for start in range(0, len(phases), 10):
        batch=phases[start:start+10]; mutations=[];variables={}
        for n,(key,phase) in enumerate(batch):
            variables['phase'+str(n)]=phase
            mutations.append(f'p{n}:updateProjectV2ItemFieldValue(input:{{projectId:"{project_id}",itemId:"{state["items"][key]}",fieldId:"{fields["Phase"]["id"]}",value:{{text:$phase{n}}}}}){{projectV2Item{{id}}}}')
        declarations=','.join('$'+key+':String!' for key in variables)
        graphql('mutation('+declarations+'){'+''.join(mutations)+'}',variables)
        completed.update(key for key,_ in batch);state['phases']=sorted(completed);save(Path(state_path),state)
        print(json.dumps({'phase_assignments':len(completed)}),flush=True)
        time.sleep(8)
    edges=set()
    for key,full in data['full'].items():
        for other in full['detail'].get('relations',{}).get('blockedBy',[]): edges.add((key,other['id']))
        for other in full['detail'].get('relations',{}).get('blocks',[]): edges.add((other['id'],key))
    # Query existing dependencies before any retry; native link creates must not
    # assume that a lost response means the mutation was rejected.
    existing=set()
    reverse={value['node_id']:key for key,value in state['issues'].items()}
    keys=sorted({key for key,_ in edges})
    for start in range(0,len(keys),20):
        batch=keys[start:start+20]
        query='query{'+''.join(f'i{n}:node(id:"{state["issues"][key]["node_id"]}"){{... on Issue{{blockedBy(first:100){{nodes{{id}}}}}}}}' for n,key in enumerate(batch))+'}'
        result=graphql(query,{})
        for n,key in enumerate(batch):
            existing.update((key,reverse[i['id']]) for i in result['i'+str(n)]['blockedBy']['nodes'] if i['id'] in reverse)
    missing=sorted(edges-existing)
    for start in range(0,len(missing),10):
        batch=missing[start:start+10]
        mutations=''.join(f'b{n}:addBlockedBy(input:{{issueId:"{state["issues"][key]["node_id"]}",blockingIssueId:"{state["issues"][other]["node_id"]}"}}){{issue{{id}}}}' for n,(key,other) in enumerate(batch))
        graphql('mutation{'+mutations+'}',{})
        existing.update(batch);state['dependencies']=[list(e) for e in sorted(existing)];save(Path(state_path),state)
        print(json.dumps({'blocking_relationships':len(existing)}),flush=True)
        time.sleep(8)
    return {'phases':len(completed),'dependencies':len(existing)}


def finish_batched(export, state_path, project_id):
    """Finish accepted issue creates with bounded batches of idempotent updates."""
    data=json.loads(Path(export).read_text());state=json.loads(Path(state_path).read_text())
    if len(state['issues']) != len(data['issues']): raise ValueError('Finish issue creation before batching updates')
    actuals={v['number']:v for v in pages(f'repos/{state["repo"]}/issues?state=all&per_page=100') if not v.get('pull_request')}
    for key,item in state['issues'].items():
        actual=actuals[item['number']];full=data['full'][key];detail=full['detail']
        desired='closed' if detail['statusType'] in {'completed','canceled'} else 'open'
        reason='not_planned' if detail['statusType']=='canceled' else 'completed' if detail['statusType']=='completed' else None
        if actual['state']==desired and actual.get('state_reason')==reason and actual['body']==render(detail,full['comments']['comments'],state['issues'],state['repo']):item['finalized']=True
    fields,_=project_fields(project_id)
    for name,kind in [('Phase','TEXT'),('Target date','DATE')]:
        if name not in fields:
            graphql('mutation($id:ID!,$name:String!,$kind:ProjectV2CustomFieldType!){createProjectV2Field(input:{projectId:$id,name:$name,dataType:$kind}){projectV2Field{... on ProjectV2Field{id}}}}',{'id':project_id,'name':name,'kind':kind})
    fields,_=project_fields(project_id)
    # Recover project additions/parent links accepted immediately before an
    # interrupted response. The stable GitHub issue IDs are the only join keys.
    reverse={v['node_id']:k for k,v in state['issues'].items()};cursor=None;existing_edges=set()
    while True:
        result=graphql('query($id:ID!,$cursor:String){node(id:$id){... on ProjectV2{items(first:100,after:$cursor){nodes{id content{... on Issue{id parent{id} blockedBy(first:100){nodes{id}}}}} pageInfo{hasNextPage endCursor}}}}}',{'id':project_id,'cursor':cursor})['node']['items']
        for item in result['nodes']:
            content=item.get('content') or {};key=reverse.get(content.get('id'))
            if not key:continue
            state['items'][key]=item['id']
            parent=data['full'][key]['detail'].get('parentId')
            if parent and (content.get('parent') or {}).get('id')==state['issues'][parent]['node_id'] and key not in state['relations']:state['relations'].append(key)
            existing_edges.update((key,reverse[v['id']]) for v in content['blockedBy']['nodes'] if v['id'] in reverse)
        if not result['pageInfo']['hasNextPage']:break
        cursor=result['pageInfo']['endCursor']
    save(Path(state_path),state)
    all_keys=sorted(data['full'],key=lambda k:int(k.split('-')[1]))
    completed=set(state.get('batch_completed',[]))
    keys=[key for key in all_keys if key not in completed]
    # Closing issues is slower than field updates on GitHub's GraphQL backend;
    # keep batches small enough to finish inside its gateway request deadline.
    for start in range(0,len(keys),3):
        batch=keys[start:start+3];mutations=[];variables={};created=[];updated=[]
        for n,key in enumerate(batch):
            item=state['issues'][key];full=data['full'][key];detail=full['detail']
            if not item.get('finalized'):
                variables['body'+str(n)]=render(detail,full['comments']['comments'],state['issues'],state['repo'])
                closed=detail['statusType'] in {'completed','canceled'}
                mutations.append(f'u{n}:updateIssue(input:{{id:"{item["node_id"]}",body:$body{n}}}){{issue{{id}}}}')
                if closed:
                    reason='NOT_PLANNED' if detail['statusType']=='canceled' else 'COMPLETED'
                    mutations.append(f'c{n}:closeIssue(input:{{issueId:"{item["node_id"]}",stateReason:{reason}}}){{issue{{id}}}}')
                updated.append(key)
            if key not in state['items']:
                mutations.append(f'a{n}:addProjectV2ItemById(input:{{projectId:"{project_id}",contentId:"{item["node_id"]}"}}){{item{{id}}}}');created.append((n,key))
        if mutations:
            declarations=','.join('$'+key+':String!' for key in variables)
            result=graphql('mutation'+('('+declarations+')' if declarations else '')+'{'+''.join(mutations)+'}',variables)
            for n,key in created:state['items'][key]=result['a'+str(n)]['item']['id']
            for key in updated:state['issues'][key]['finalized']=True
            save(Path(state_path),state)
        mutations=[];variables={};added_parents=[];edges=[]
        for n,key in enumerate(batch):
            item=state['issues'][key];detail=data['full'][key]['detail'];item_id=state['items'][key]
            status={'Todo':'Ready','In Review':'In Review','On Hold':'On Hold','Done':'Done','Canceled':'Canceled','Backlog':'Backlog'}.get(detail['status'],'In Progress')
            for field_name,value in [('Status',status),('Priority',detail['priority']['name'])]:
                field=fields[field_name];option=next(v['id'] for v in field['options'] if v['name']==value)
                mutations.append(f'{field_name.lower()}{n}:updateProjectV2ItemFieldValue(input:{{projectId:"{project_id}",itemId:"{item_id}",fieldId:"{field["id"]}",value:{{singleSelectOptionId:"{option}"}}}}){{projectV2Item{{id}}}}')
            if detail.get('projectMilestone'):
                variables['phase'+str(n)]=detail['projectMilestone']['name']
                mutations.append(f'p{n}:updateProjectV2ItemFieldValue(input:{{projectId:"{project_id}",itemId:"{item_id}",fieldId:"{fields["Phase"]["id"]}",value:{{text:$phase{n}}}}}){{projectV2Item{{id}}}}')
            if detail.get('dueDate'):
                date=detail['dueDate']
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):raise ValueError('Invalid source date')
                mutations.append(f'd{n}:updateProjectV2ItemFieldValue(input:{{projectId:"{project_id}",itemId:"{item_id}",fieldId:"{fields["Target date"]["id"]}",value:{{date:"{date}"}}}}){{projectV2Item{{id}}}}')
            parent=detail.get('parentId')
            if parent and key not in state['relations']:
                mutations.append(f'parent{n}:addSubIssue(input:{{issueId:"{state["issues"][parent]["node_id"]}",subIssueId:"{item["node_id"]}"}}){{issue{{id}}}}');added_parents.append(key)
            for other in detail.get('relations',{}).get('blockedBy',[]):
                edge=(key,other['id'])
                if edge in existing_edges:continue
                alias='b'+str(len(edges));edges.append(edge)
                mutations.append(f'{alias}:addBlockedBy(input:{{issueId:"{item["node_id"]}",blockingIssueId:"{state["issues"][other["id"]]["node_id"]}"}}){{issue{{id}}}}')
        declarations=','.join('$'+key+':String!' for key in variables)
        graphql('mutation'+('('+declarations+')' if declarations else '')+'{'+''.join(mutations)+'}',variables)
        for key in batch:state['issues'][key]['fields_set']=True
        state['relations'].extend(added_parents);existing_edges.update(edges)
        state['phases']=sorted(set(state.get('phases',[])) | {k for k in batch if data['full'][k]['detail'].get('projectMilestone')})
        completed.update(batch);state['batch_completed']=sorted(completed)
        state['dependencies']=[list(e) for e in sorted(existing_edges)];save(Path(state_path),state)
        print(json.dumps({'project_records_verified':len(completed),'total':len(all_keys)}),flush=True)
        time.sleep(3)
    state['completed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save(Path(state_path),state)
    return {'issues':len(all_keys),'phases':len(state['phases']),'parents':len(state['relations']),'dependencies':len(state['dependencies'])}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export',type=Path,required=True);parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--repo',default='dealpipe/dealpipe');parser.add_argument('--project-id',required=True);parser.add_argument('--apply',action='store_true')
    parser.add_argument('--finish-batched',action='store_true')
    args=parser.parse_args()
    if args.finish_batched:
        for attempt in range(3):
            try:
                print(json.dumps(finish_batched(args.export,args.state,args.project_id)))
                break
            except RuntimeError as error:
                if attempt==2 or not any(code in str(error) for code in ['HTTP 502','HTTP 503','HTTP 504']):raise
                print('Transient GitHub gateway failure; recovering accepted updates before retry.',flush=True)
                time.sleep(5)
    elif args.apply:
        migrate(args.export,args.state,args.repo,args.project_id)
        sync_phases_dependencies(args.export,args.state,args.project_id)
        print(json.dumps({'native_due_dates':sync_due_dates(args.export,args.state,args.project_id),
                          'phase_definitions':sync_milestone_definitions(args.export,args.state)}))
    else:
        data=json.loads(args.export.read_text());print(json.dumps({'issues':len(data['issues']),'projects':len(data['projects']),'comments':sum(len(v['comments']['comments']) for v in data['full'].values()),'destination':args.repo,'project_id':args.project_id}))
