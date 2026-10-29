"""Offline self-contained wheel/import/default-disabled validation.

Copies only acp_bridge; builds without network/dependency resolution; installs
only this wheel into a fresh venv. It deliberately does NOT install ACP SDK or
Pydantic, so no real ACP execution is claimed here. Approved fake SDK execution
is separately tested by verify_codex.py. Build tools must already be installed.
"""
from __future__ import annotations
import argparse
import configparser
from email.parser import Parser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile

ROOT=Path(__file__).resolve().parents[1]
GUARD=r'''
import os,sys,socket,threading
# asyncio uses a local socketpair as a wakeup pipe, including for a completely
# offline stdio server. Permit only the stdlib socketpair constructor's bounded
# local IPC setup, not ordinary socket creation/connect/bind from the product.
_pair_state=threading.local()
_original_pair=socket.socketpair
def _local_pair(*args,**kwargs):
    _pair_state.active=True
    try:return _original_pair(*args,**kwargs)
    finally:_pair_state.active=False
socket.socketpair=_local_pair

def guard(event,args):
    if event == 'import' and (args[0] in ('acp','pydantic') or args[0].startswith(('acp.','pydantic.'))):
        raise AssertionError('SDK/Pydantic imported by an offline inspection path')
    if event.startswith('socket.') and not getattr(_pair_state,'active',False):
        raise AssertionError('Forbidden network operation: '+event)
    if event == 'sqlite3.connect' or event.startswith('subprocess.'):
        raise AssertionError('Forbidden runtime side effect: '+event)
    if event in ('os.system','os.posix_spawn','os.fork','os.forkpty','os.exec','os.spawn','os.mkdir','os.remove','os.rmdir','os.rename','os.link','os.symlink','os.truncate'):
        raise AssertionError('Forbidden runtime side effect: '+event)
    if event == 'open':
        mode=args[1] if len(args)>1 else None;flags=args[2] if len(args)>2 else 0
        if (isinstance(mode,str) and any(x in mode for x in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
            raise AssertionError('Forbidden runtime file mutation')
sys.addaudithook(guard)
'''


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def run(evidence):
    evidence.mkdir(parents=True,exist_ok=True)
    if any(evidence.iterdir()):raise ValueError('Use a fresh empty evidence directory')
    work=Path(tempfile.mkdtemp(prefix='acp-wheel-'));home=work/'home';probe=work/'probe';guard=work/'guard'
    for p in (home,probe,guard):p.mkdir(mode=0o700)
    env={'HOME':str(home),'USERPROFILE':str(home),'PATH':os.pathsep.join([str(Path(sys.executable).parent),'/usr/bin','/bin']),
         'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8',
         'PIP_NO_INDEX':'1','PIP_CONFIG_FILE':os.devnull,'PIP_DISABLE_PIP_VERSION_CHECK':'1',
         'HTTP_PROXY':'http://127.0.0.1:9','HTTPS_PROXY':'http://127.0.0.1:9','ALL_PROXY':'http://127.0.0.1:9','NO_PROXY':'127.0.0.1,localhost'}
    if os.name=='nt':env['SystemRoot']=os.environ.get('SystemRoot',r'C:\Windows')
    report={'status':'running','temporary_work':str(work),'python':sys.version,'probes':[],
            'copied_only_acp_bridge':True,'dependencies_installed':False,'real_api_calls':0,
            'sdk_execution_tested':False,'environment':'new HOME, no API keys; pip no-index/no-deps/no-build-isolation'}
    report['network_guard']='Socket operations denied except stdlib local socketpair for asyncio wakeup IPC'
    initial={p.relative_to(ROOT).as_posix():sha(p) for p in sorted((ROOT/'agent_bus_acp').rglob('*.py'))}
    def command(label,argv,*,cwd=probe,environment=env,stdin=None):
        r=subprocess.run(argv,cwd=cwd,env=environment,input=stdin,text=True,encoding='utf-8',capture_output=True,timeout=90)
        (evidence/(label+'.stdout.txt')).write_text(r.stdout);(evidence/(label+'.stderr.txt')).write_text(r.stderr)
        report['probes'].append({'name':label,'returncode':r.returncode})
        assert r.returncode==0,label+': '+r.stderr[-2000:]
        return r
    try:
        report['build_tools']={n:importlib.metadata.version(n) for n in ('pip','setuptools','wheel')}
        stage=work/'standalone-copy'
        shutil.copytree(ROOT,stage,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc','build','dist','*.egg-info'))
        assert not (stage.parent/'light').exists() and not (stage.parent/'mcp_agent_bus').exists()
        wheels=evidence/'dist';wheels.mkdir()
        command('wheel-build',[sys.executable,'-m','pip','wheel','--no-index','--no-deps','--no-build-isolation','--wheel-dir',str(wheels),str(stage)])
        choices=list(wheels.glob('*.whl'));assert len(choices)==1;wheel=choices[0]
        with zipfile.ZipFile(wheel) as z:
            members=sorted(z.namelist());assert 'agent_bus_acp/__init__.py' in members
            assert not any(x.startswith(('mcp_agent_bus/','mcp_agent_bus_light/','acp/','pydantic/')) for x in members)
            source={p.relative_to(stage).as_posix():sha(p) for p in sorted((stage/'agent_bus_acp').rglob('*.py'))}
            for name,digest in source.items():assert hashlib.sha256(z.read(name)).hexdigest()==digest,name
            entry=next(x for x in members if x.endswith('.dist-info/entry_points.txt'));config=configparser.ConfigParser();config.read_string(z.read(entry).decode())
            assert dict(config['console_scripts'])=={'mcp-agent-bus-acp':'agent_bus_acp.cli:main'}
            metadata=Parser().parsestr(z.read(next(x for x in members if x.endswith('.dist-info/METADATA'))).decode())
            assert metadata['Name']=='mcp-agent-bus-acp'
            assert set(metadata['Requires-Python'].split(','))=={'>=3.10','<3.15'}
            requires=metadata.get_all('Requires-Dist');assert set(requires)=={'agent-client-protocol==0.12.1','pydantic==2.13.4'},requires
            notices={}
            for filename in ('LICENSE','ATTRIBUTION.md'):
                found=[x for x in members if '.dist-info/' in x and x.endswith('/'+filename)];assert len(found)==1
                assert z.read(found[0])==(stage/filename).read_bytes();notices[filename]={'wheel_path':found[0],'sha256':sha(stage/filename)}
            modules=[x[:-3].replace('/','.').removesuffix('.__init__') for x in members if x.startswith('agent_bus_acp/') and x.endswith('.py') and not x.endswith('/__main__.py')]
        report.update(wheel=wheel.name,wheel_sha256=sha(wheel),wheel_bytes=wheel.stat().st_size,
                      source_sha256=source,pyproject_sha256=sha(stage/'pyproject.toml'),requires_dist=requires,
                      notices=notices,wheel_members=members,modules=modules)
        target=work/'venv';venv.EnvBuilder(with_pip=False).create(target)
        bin_dir=target/('Scripts' if os.name=='nt' else 'bin');python=bin_dir/('python.exe' if os.name=='nt' else 'python')
        command('ensurepip',[str(python),'-Im','ensurepip','--upgrade','--default-pip'])
        command('wheel-install',[str(python),'-m','pip','install','--no-index','--no-deps',str(wheel)])
        (guard/'sitecustomize.py').write_text(GUARD);safe={**env,'PYTHONPATH':str(guard)}
        imports="import importlib,importlib.util,pathlib,json; expected=pathlib.Path("+repr(str(target))+ ").resolve();found={}\n"
        imports+="assert all(importlib.util.find_spec(n) is None for n in ('acp','pydantic','mcp_agent_bus','mcp_agent_bus_light'))\n"
        imports+='for name in '+repr(modules)+":\n m=importlib.import_module(name);p=pathlib.Path(m.__file__).resolve();assert p.is_relative_to(expected);found[name]=str(p)\nprint(json.dumps(found))"
        command('installed-imports-no-sdk-side-effects',[str(python),'-c',imports],environment=safe)
        executable=bin_dir/('mcp-agent-bus-acp.exe' if os.name=='nt' else 'mcp-agent-bus-acp')
        command('console-help',[str(executable),'--help'],environment=safe)
        command('module-help',[str(python),'-m','agent_bus_acp','--help'],environment=safe)
        command('serve-help',[str(executable),'serve','--help'],environment=safe)
        caps=json.loads(command('capabilities',[str(executable),'capabilities'],environment=safe).stdout)
        assert caps['default_process_enabled'] is False and caps['acp_protocol']==1
        report['capabilities']=caps
        profile={'name':'offline_fixture','argv':[str(python),str(stage/'examples/fake_acp_agent.py')],'cwd':str(probe)}
        profile_file=work/'profile.json';profile_file.write_text(json.dumps(profile));state=work/'must-not-be-created'
        def request(ident,method,params=None):return {'jsonrpc':'2.0','id':ident,'method':method,'params':params or {}}
        init=request(1,'initialize',{'protocolVersion':'2025-06-18'})
        listing=request(2,'tools/list')
        args={'profile':profile['name'],'text':'Synthetic packaging preview only.'}
        preview=request(3,'tools/call',{'name':'acp_preview','arguments':args})
        rejected=request(4,'tools/call',{'name':'acp_prompt','arguments':{**args,'user_approved_agent_run':False,'approved_request_sha256':'0'*64}})
        def stdio(label,argv,messages,cwd=probe):
            response=command(label,argv,cwd=cwd,environment=safe,stdin=''.join(json.dumps(x)+'\n' for x in messages))
            return {x['id']:x for x in map(json.loads,response.stdout.splitlines()) if 'id' in x}
        default=stdio('default-disabled-stdio',[str(executable),'serve'],[init,listing])
        assert {x['name'] for x in default[2]['result']['tools']}=={'acp_preview','acp_prompt','acp_status','acp_cancel'}
        argv=[str(executable),'serve','--profile-file',str(profile_file),'--state-dir',str(state)]
        configured=stdio('configured-preview-unapproved-stdio',argv,[init,listing,preview,rejected])
        assert configured[3]['result']['isError'] is False
        prepared=configured[3]['result']['structuredContent'];assert prepared['agent_process_enabled'] is False
        assert configured[4]['result']['isError'] is True and 'APPROVAL' in json.dumps(configured[4])
        approved=request(5,'tools/call',{'name':'acp_prompt','arguments':{**args,'user_approved_agent_run':True,'approved_request_sha256':prepared['request_sha256']}})
        disabled=stdio('approved-but-operator-disabled-stdio',argv,[init,approved])
        assert disabled[5]['result']['isError'] is True and 'AGENT_PROCESS_DISABLED' in json.dumps(disabled[5])
        assert not state.exists() and not any(probe.iterdir())
        # A second fresh venv has no installed package at all: cwd alone supplies
        # the copied source; no parent package or mounted SDK is on PYTHONPATH.
        source_env=work/'source-only-venv';venv.EnvBuilder(with_pip=False).create(source_env)
        source_python=source_env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        command('copied-source-help',[str(source_python),'-m','agent_bus_acp','--help'],cwd=stage,environment=safe)
        source_caps=json.loads(command('copied-source-capabilities',[str(source_python),'-m','agent_bus_acp','capabilities'],cwd=stage,environment=safe).stdout);assert source_caps==caps
        source_preview=stdio('copied-source-preview',[str(source_python),'-m','agent_bus_acp','serve','--profile-file',str(profile_file)], [init,preview],stage)
        assert source_preview[3]['result']['structuredContent']==prepared
        report['inspection_without_sdk_or_pydantic']=True
        report['source_copy_direct_run_passed']=True
        report['state_directory_not_created']=not state.exists()
        report['probe_directory_empty']=not any(probe.iterdir())
        report['installed_location']=str(target)
        report['source_unchanged_during_test']=initial=={p.relative_to(ROOT).as_posix():sha(p) for p in sorted((ROOT/'agent_bus_acp').rglob('*.py'))}
        assert report['source_unchanged_during_test'],'Runtime changed; rerun after freeze'
        report['status']='passed'
    except BaseException as exc:
        report.update(status='failed',error=type(exc).__name__+': '+str(exc));raise
    finally:
        (evidence/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='wheel_members'},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence-dir',type=Path,required=True);a=p.parse_args();run(a.evidence_dir.resolve())
