"""Zero-cost real Codex CLI → MCP bridge → official ACP SDK → fake subprocess.

Runs only original fixed-response fixtures in isolated homes. No provider/model
turn, real API key, user Codex config, or real Harness is used. This checks wire
integration, not host approval UI, provider billing, or user-visible streaming.
Requires the pinned ACP SDK already installed or supplied with --sdk-source.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
TEXT='Fixed synthetic ACP integration probe. No private input.'
SCENARIOS=('disabled','happy','callbacks','cancel','disconnect')


def write_json(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')


def hashes(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root/'agent_bus_acp').rglob('*.py'))}


class Client:
    def __init__(self,codex,env,cwd,evidence):
        self.events=[];self.ident=0;self.q=queue.Queue();self.saved={}
        self.err=(evidence/'cli.stderr.txt').open('w',encoding='utf-8')
        self.p=subprocess.Popen([codex,'app-server','--stdio'],env=env,cwd=cwd,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.err,text=True,encoding='utf-8',bufsize=1)
        def read():
            for line in self.p.stdout:
                try:self.q.put(json.loads(line))
                except json.JSONDecodeError:self.q.put({'invalid_stdout':line})
            self.q.put({'eof':True})
        threading.Thread(target=read,daemon=True).start()
    def start(self,method,params):
        self.ident+=1
        message={'id':self.ident,'method':method,'params':params}
        self.events.append({'sent':message});self.p.stdin.write(json.dumps(message)+'\n');self.p.stdin.flush()
        return self.ident
    def finish(self,ident,timeout=30):
        deadline=time.monotonic()+timeout
        while ident not in self.saved:
            message=self.q.get(timeout=max(.01,deadline-time.monotonic()));self.events.append({'received':message})
            if message.get('eof'):raise RuntimeError('Unexpected app-server EOF')
            if 'id' in message:self.saved[message['id']]=message
        result=self.saved.pop(ident)
        if 'error' in result:raise RuntimeError(json.dumps(result['error']))
        return result['result']
    def call(self,method,params):return self.finish(self.start(method,params))
    def notify(self,method):
        self.p.stdin.write(json.dumps({'method':method})+'\n');self.p.stdin.flush()
    def close(self):
        self.p.terminate()
        try:self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:self.p.kill();self.p.wait()
        self.err.close()


def payload(result):
    assert result.get('isError') is not True,result
    if isinstance(result.get('structuredContent'),dict):return result['structuredContent']
    return json.loads(result['content'][0]['text'])


def assert_error(result,text):
    assert result.get('isError') is True and text in json.dumps(result),result


def run(codex,evidence,sdk_source):
    evidence.mkdir(parents=True,exist_ok=True)
    if any(evidence.iterdir()):raise ValueError('Use a new empty evidence directory; prior runs are preserved')
    home=Path(tempfile.mkdtemp(prefix='acp-bridge-codex-home-'))
    env={'HOME':str(home),'USERPROFILE':str(home),'CODEX_HOME':str(home),
         'PATH':os.pathsep.join([str(Path(sys.executable).parent),'/usr/bin','/bin']),
         'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8',
         'HTTP_PROXY':'http://127.0.0.1:9','HTTPS_PROXY':'http://127.0.0.1:9',
         'ALL_PROXY':'http://127.0.0.1:9','NO_PROXY':'127.0.0.1,localhost'}
    if os.name=='nt':env['SystemRoot']=os.environ.get('SystemRoot',r'C:\Windows')
    paths=[str(ROOT)]
    if sdk_source:
        assert (sdk_source/'acp/__init__.py').is_file(),'SDK source unavailable'
        paths.append(str(sdk_source))
    pythonpath=os.pathsep.join(paths)
    version=subprocess.check_output([codex,'--version'],env=env,text=True,timeout=10).strip()
    report={'status':'running','codex_version':version,'source_sha256':hashes(ROOT),
            'fixture_sha256':hashlib.sha256((ROOT/'examples/fake_acp_agent.py').read_bytes()).hexdigest(),
            'sdk_source':str(sdk_source) if sdk_source else 'installed dependency',
            'real_api_calls':0,'model_turns':0,'real_harness_tested':False,
            'host_approval_ui_tested':False,'user_visible_streaming_tested':False,'temporary_home':str(home),'checks':{}}
    if sdk_source:
        report['sdk_source_sha256']={p.relative_to(sdk_source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted((sdk_source/'acp').rglob('*.py'))}
    cases={}
    for scenario in SCENARIOS:
        directory=evidence/scenario;directory.mkdir()
        workspace=directory/'workspace';workspace.mkdir(mode=0o700)
        state=directory/'state';state.mkdir(mode=0o700)
        wire=directory/'acp-wire.jsonl'
        profile={'name':'fixture_'+scenario,'argv':[sys.executable,str(ROOT/'examples/fake_acp_agent.py'),
                 '--scenario','happy' if scenario=='disabled' else scenario,'--trace',str(wire)],
                 'cwd':str(workspace),'pass_env':[],'timeout_seconds':10,'max_output_bytes':4096,'max_calls':1}
        profile_path=directory/'profile.json';write_json(profile_path,profile)
        alias='acp_'+scenario
        argv=[codex,'mcp','add',alias,'--env','PYTHONPATH='+pythonpath,'--',sys.executable,'-m','agent_bus_acp','serve',
              '--profile-file',str(profile_path),'--state-dir',str(state)]
        if scenario!='disabled':argv.append('--allow-agent-process')
        registered=subprocess.run(argv,env=env,cwd=ROOT,capture_output=True,text=True,timeout=15)
        assert registered.returncode==0,registered.stderr
        write_json(directory/'registration.json',{'returncode':registered.returncode,'stdout':registered.stdout,'stderr':registered.stderr})
        cases[scenario]={'alias':alias,'profile':profile['name'],'wire':wire,'state':state}
    config=home/'config.toml'
    config.write_text('model_provider="offline"\nmodel="no-inference"\ncli_auth_credentials_store="ephemeral"\n'+config.read_text()+
        '\n[model_providers.offline]\nname="Local fake ACP only"\nbase_url="http://127.0.0.1:9/v1"\nwire_api="responses"\nrequires_openai_auth=false\nrequest_max_retries=0\n[analytics]\nenabled=false\n')
    c=Client(codex,env,ROOT,evidence)
    try:
        report['initialize']=c.call('initialize',{'clientInfo':{'name':'acp_bridge_offline_probe','version':'0.1.0'},'capabilities':{'experimentalApi':True}})
        c.notify('initialized')
        def new_thread():
            return c.call('thread/start',{'cwd':str(ROOT),'ephemeral':True,'modelProvider':'offline','model':'no-inference',
                                         'sandbox':'read-only','approvalPolicy':'on-request','environments':[]})['thread']['id']
        thread=new_thread()
        def tool(scenario,name,arguments,tid=thread,wait=True):
            ident=c.start('mcpServer/tool/call',{'threadId':tid,'server':cases[scenario]['alias'],'tool':name,'arguments':arguments})
            return c.finish(ident) if wait else ident
        def wire(scenario):
            p=cases[scenario]['wire']
            return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
        def prompt_count(scenario):
            return sum(x.get('received',{}).get('method')=='session/prompt' for x in wire(scenario))
        approved={}
        for scenario in SCENARIOS:
            listing=c.call('mcpServerStatus/list',{'threadId':thread,'serverName':cases[scenario]['alias'],'detail':'toolsAndAuthOnly'})
            write_json(evidence/scenario/'discovery.json',listing)
            server=next(x for x in listing['data'] if x['name']==cases[scenario]['alias'])
            names=set(server['tools']) if isinstance(server['tools'],dict) else {x['name'] for x in server['tools']}
            assert names=={'acp_preview','acp_prompt','acp_status','acp_cancel'},names
            args={'profile':cases[scenario]['profile'],'text':TEXT}
            preview=payload(tool(scenario,'acp_preview',args))
            assert preview['agent_process_enabled'] is (scenario!='disabled')
            assert not wire(scenario),'Preview launched an agent'
            assert not any(cases[scenario]['state'].iterdir()),'Preview persisted state'
            approved[scenario]={**args,'user_approved_agent_run':True,'approved_request_sha256':preview['request_sha256']}
            write_json(evidence/scenario/'preview.json',preview)
        report['checks']['all_discovery_and_offline_preview']=True
        missing=tool('happy','acp_prompt',{**approved['happy'],'user_approved_agent_run':False})
        assert_error(missing,'EXPLICIT_HUMAN_APPROVAL_REQUIRED')
        mismatch=tool('happy','acp_prompt',{**approved['happy'],'approved_request_sha256':'0'*64})
        assert_error(mismatch,'APPROVAL_FINGERPRINT_MISMATCH')
        injected=tool('happy','acp_prompt',{**approved['happy'],'command':'not-allowed'})
        assert_error(injected,'INVALID_TOOL_ARGUMENTS')
        assert not wire('happy') and not any(cases['happy']['state'].iterdir())
        disabled=tool('disabled','acp_prompt',approved['disabled']);assert_error(disabled,'AGENT_PROCESS_DISABLED')
        assert not wire('disabled') and not any(cases['disabled']['state'].iterdir())
        report['checks']['approval_hash_arguments_and_operator_gate']=True
        happy=payload(tool('happy','acp_prompt',approved['happy']))
        assert happy['status']=='completed' and happy['text']=='fixture answer',happy
        assert prompt_count('happy')==1
        exhausted=tool('happy','acp_prompt',approved['happy']);assert_error(exhausted,'OPERATOR_CALL_LIMIT_EXHAUSTED')
        assert prompt_count('happy')==1
        report['checks']['happy_official_sdk_wire_and_call_limit']=True
        denied=payload(tool('callbacks','acp_prompt',approved['callbacks']))
        assert denied['text']=='FIXTURE_CALLBACKS_DENIED'
        callback_wire=wire('callbacks');answers={x['received']['id']:x['received'] for x in callback_wire if 'id' in x.get('received',{}) and 'method' not in x['received']}
        assert answers['permission_1']['result']=={'outcome':{'outcome':'cancelled'}}
        assert answers['fs_1']['error']['code']==-32601 and answers['terminal_1']['error']['code']==-32601
        report['checks']['permission_filesystem_terminal_denied']=True
        held=tool('cancel','acp_prompt',approved['cancel'],wait=False)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            status=payload(tool('cancel','acp_status',{}))
            if status.get('state')=='prompting' and status.get('output_bytes',0)>0:break
            time.sleep(.02)
        else:raise AssertionError('Cancel fixture did not become active')
        wrong_cancel=tool('cancel','acp_cancel',{'run_id':'0'*32});assert_error(wrong_cancel,'NO_MATCHING_ACTIVE_RUN')
        busy=tool('cancel','acp_prompt',approved['cancel']);assert_error(busy,'BUSY')
        cancellation=payload(tool('cancel','acp_cancel',{'run_id':status['run_id']}))
        assert cancellation['cancel_requested'] is True and cancellation['cancel_confirmed'] is False
        cancelled=payload(c.finish(held))
        assert cancelled['status']=='cancelled' and cancelled['cancel_confirmed'] is True
        assert 'late-before-cancel-completion' in cancelled['text']
        assert prompt_count('cancel')==1
        assert any(x.get('received',{}).get('method')=='session/cancel' and 'id' not in x['received'] for x in wire('cancel'))
        final_status=payload(tool('cancel','acp_status',{}));assert final_status['local_child_reaped'] is True
        report['checks']['concurrent_explicit_cancel_confirmed_and_reaped']=True
        unknown=tool('disconnect','acp_prompt',approved['disconnect']);assert_error(unknown,'UNKNOWN')
        assert prompt_count('disconnect')==1
        repeat=tool('disconnect','acp_prompt',approved['disconnect']);assert_error(repeat,'PREVIOUS_OUTCOME_UNKNOWN_OR_INFLIGHT')
        assert prompt_count('disconnect')==1
        old_status=payload(tool('disconnect','acp_status',{}));assert old_status['state']=='unknown'
        # A new real CLI thread opens a new MCP bridge process but shares the
        # private metadata ledger, proving the unknown quarantine survives it.
        second_thread=new_thread()
        c.call('mcpServerStatus/list',{'threadId':second_thread,'serverName':cases['disconnect']['alias'],'detail':'toolsAndAuthOnly'})
        new_status=payload(tool('disconnect','acp_status',{},tid=second_thread))
        assert new_status['state']=='idle', 'New thread unexpectedly reused the old bridge instance'
        reopened=tool('disconnect','acp_prompt',approved['disconnect'],tid=second_thread)
        assert_error(reopened,'PREVIOUS_OUTCOME_UNKNOWN_OR_INFLIGHT');assert prompt_count('disconnect')==1
        report['checks']['unknown_no_replay_across_bridge_restart']=True
        for scenario in ('happy','callbacks','cancel','disconnect'):
            assert TEXT.encode() not in (cases[scenario]['state']/'quarantine.sqlite').read_bytes(),'Prompt text persisted in ledger'
        report['checks']['ledger_has_no_literal_prompt']=True
        write_json(evidence/'call-results.json',{'missing_approval':missing,'hash_mismatch':mismatch,'injected_argument':injected,
            'disabled':disabled,'happy':happy,'exhausted':exhausted,'callbacks':denied,'wrong_cancel':wrong_cancel,'busy':busy,
            'cancel_requested':cancellation,'cancelled':cancelled,'final_status':final_status,'unknown':unknown,'repeat':repeat,
            'original_bridge_status':old_status,'new_bridge_status':new_status,'reopened':reopened})
        report['source_unchanged_during_test']=report['source_sha256']==hashes(ROOT)
        assert report['source_unchanged_during_test'],'Runtime source changed during verification; rerun after freeze'
        report['status']='passed'
    except BaseException as exc:
        report.update(status='failed',error=type(exc).__name__+': '+str(exc));raise
    finally:
        c.close();write_json(evidence/'app-server-events.json',c.events);write_json(evidence/'summary.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='sdk_source_sha256'},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--codex',default='/opt/codex/bin/codex')
    p.add_argument('--evidence-dir',type=Path,required=True)
    p.add_argument('--sdk-source',type=Path,help='Already materialized official SDK src directory; never downloads')
    a=p.parse_args();run(a.codex,a.evidence_dir.resolve(),a.sdk_source.resolve() if a.sdk_source else None)
