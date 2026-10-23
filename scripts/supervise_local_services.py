"""Own the four local listening services without submitting generation work.

--once only inspects/adopts and writes supervisor state. --run also starts
missing services with bounded retries. A live unresponsive process is never
killed. Unknown bind owners are never adopted, stopped or displaced.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Service:
    name: str
    python: Path
    script: Path
    options: tuple
    port: int
    health: dict

    def command(self):
        return [str(self.python), '-X', 'utf8', '-u', str(self.script), *self.options]


def specs(root,config):
    value=json.loads(config.read_text('utf-8-sig'))
    if value.get('schema')!=1 or not isinstance(value.get('services'),list): raise ValueError('Invalid service config.')
    services=[]
    for item in value['services']:
        python=(root/item['python']).resolve(); script=(root/item['script']).resolve()
        if not python.is_relative_to(root) or not script.is_relative_to(root): raise ValueError('Service paths must stay in the workspace.')
        args=item['args']; health=item['health']; port=item['port']; name=item['name']
        if (not isinstance(args,list) or not all(isinstance(a,str) for a in args) or '--parent-pipe' in args
                or type(port) is not int or not 1024<=port<=65535
                or not isinstance(name,str) or not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name)
                or not health['path'].startswith('/') or health.get('method','GET') not in ('GET','POST')):
            raise ValueError('Invalid service definition.')
        if health.get('method')=='POST' and health.get('body')!={'op':'settings','data':{}}:
            raise ValueError('Only read-only settings health requests are permitted.')
        if script==root/'runtime/server.py' and '--no-auto-resume' not in args: args=[*args,'--no-auto-resume']
        services.append(Service(name,python,script,tuple(args),port,health))
    if len({s.name for s in services})!=len(services) or len({s.port for s in services})!=len(services):
        raise ValueError('Duplicate service name or port.')
    return services


def split_command(value):
    argc=ctypes.c_int()
    parse=ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_int)]
    parse.restype=ctypes.POINTER(wintypes.LPWSTR)
    pointer=parse(value,ctypes.byref(argc))
    if not pointer: return []
    try: return [pointer[i] for i in range(argc.value)]
    finally:
        free=ctypes.windll.kernel32.LocalFree
        free.argtypes=[ctypes.c_void_p]; free.restype=ctypes.c_void_p
        free(pointer)


def normalized(value):
    return os.path.normcase(str(value).replace('/',os.sep)).rstrip('\\/')


def matching(service, process, root):
    """Exact argv, interpreter and workspace binding, allowing old resume default."""
    arguments=process.get('argv') or split_command(process.get('CommandLine') or '')
    permitted={normalized(service.python)}
    config=service.python.parent.parent/'pyvenv.cfg'
    if config.is_file():
        for line in config.read_text('utf-8').splitlines():
            if line.startswith('home = '): permitted.add(normalized(Path(line[7:])/'python.exe'))
    if len(arguments)<5 or normalized(arguments[0]) not in permitted: return False
    if arguments[1:4]!=['-X','utf8','-u']: return False
    script=Path(arguments[4])
    if not script.is_absolute(): script=root/script
    if normalized(script)!=normalized(service.script): return False
    actual=arguments[5:]
    expected=list(service.options)
    # Existing healthy runtime was explicitly launched before this opt-in flag.
    if service.name=='runtime' and '--no-auto-resume' not in actual:
        expected.remove('--no-auto-resume')
    if len(actual)!=len(expected) or any(normalized(a)!=normalized(b) for a,b in zip(actual,expected)): return False
    executable=normalized(process.get('ExecutablePath') or '')
    return executable in permitted


def snapshot(ports):
    script="$ErrorActionPreference='Stop'; $ports=@("+','.join(str(p) for p in ports)+");\n"+"""
$owners=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {$_.LocalPort -in $ports} | Select-Object LocalPort,OwningProcess);
$processes=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^python(w)?\\.exe$'} | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,CreationDate);
@{listeners=$owners;processes=$processes} | ConvertTo-Json -Depth 5 -Compress"""
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,encoding='utf-8',timeout=20,creationflags=0x08000000)
    if result.returncode: raise RuntimeError('Could not inspect Windows service ownership.')
    value=json.loads(result.stdout.lstrip('\ufeff'))
    return value['listeners'],value['processes']


def probe(service,root):
    headers={}; data=None
    try:
        auth=service.health.get('auth')
        if auth:
            path=(root/auth['path']).resolve()
            if not path.is_relative_to(root): return False
            if auth['type']=='bearer_file': headers['Authorization']='Bearer '+path.read_text('ascii').strip()
            elif auth['type']=='basic_json':
                credentials=json.loads(path.read_text('utf-8'))
                secret=(credentials['username']+':'+credentials['password']).encode()
                headers['Authorization']='Basic '+base64.b64encode(secret).decode()
            else: return False
        if service.health.get('method')=='POST':
            data=json.dumps(service.health['body']).encode(); headers['Content-Type']='application/json'
            headers['Origin']=f'http://127.0.0.1:{service.port}'
        with urlopen(Request(f'http://127.0.0.1:{service.port}'+service.health['path'],data,headers),timeout=4) as response:
            payload=json.loads(response.read(1024*1024))
            return response.status==200 and isinstance(payload,dict) and not payload.get('error')
    except (URLError,OSError,ValueError): return False


def atomic(path,value):
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    os.replace(temporary,path)


class Supervisor:
    def __init__(self,root,config,*,inspect=None,health=probe,clock=time.time):
        self.root=root.resolve(); self.folder=self.root/'training-work/local-services'
        self.folder.mkdir(parents=True,exist_ok=True)
        self.services=specs(self.root,config); self.inspect=inspect or (lambda:snapshot([s.port for s in self.services])); self.health=health; self.clock=clock
        try: self.state=json.loads((self.folder/'state.json').read_text('utf-8'))
        except (OSError,ValueError): self.state={'schema':1,'services':{}}
        self.children={}

    def event(self,name,status):
        # No argv, HTTP authorization, session token, credentials or response body.
        with (self.folder/'supervisor.log').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'time':self.clock(),'service':name,'status':status})+'\n')

    def launch(self,service):
        if not service.python.is_file() or not service.script.is_file():
            raise RuntimeError('Service interpreter or script is missing.')
        if service.name=='gateway' and not self.state['services'].get('runtime',{}).get('healthy'):
            raise RuntimeError('Runtime dependency is not healthy.')
        stdout=(self.folder/(service.name+'.stdout.log')).open('ab')
        stderr=(self.folder/(service.name+'.stderr.log')).open('ab')
        try:
            process=subprocess.Popen(service.command(),cwd=self.root,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                                     creationflags=0x00000008|0x00000200|0x08000000,close_fds=True)
            self.children[service.name]=process
            return process.pid
        finally: stdout.close(); stderr.close()

    def cycle(self,repair=False):
        listeners,processes=self.inspect()
        by_pid={p['ProcessId']:p for p in processes}; timestamp=self.clock()
        result={}
        for service in self.services:
            previous=self.state['services'].get(service.name,{})
            attempts=[t for t in previous.get('attempts',[]) if timestamp-t<3600]
            item={'port':service.port,'healthy':False,'attempts':attempts,'next_attempt':previous.get('next_attempt',0)}
            owners={p['OwningProcess'] for p in listeners if p['LocalPort']==service.port}
            matches=[p for p in processes if matching(service,p,self.root)]
            if owners:
                if len(owners)!=1 or not all(pid in by_pid and matching(service,by_pid[pid],self.root) for pid in owners):
                    item['status']='foreign_port_owner'
                else:
                    process=by_pid[next(iter(owners))]
                    item.update(pid=process['ProcessId'],created=process.get('CreationDate'),
                                identity=hashlib.sha256((process.get('CommandLine') or '').encode()).hexdigest())
                    item['healthy']=self.health(service,self.root)
                    item['status']='healthy' if item['healthy'] else 'unresponsive_owned_process'
            elif matches:
                # A verified service is starting, or is alive without a bind.
                # It may own work: never kill it or spawn a duplicate.
                item.update(status='owned_process_without_listener',pid=matches[-1]['ProcessId'])
            elif service.name in self.children and self.children[service.name].poll() is None:
                item.update(status='starting',pid=self.children[service.name].pid)
            elif not repair:
                item['status']='missing'
            elif len(attempts)>=5:
                item['status']='restart_budget_exhausted'
            elif timestamp<item['next_attempt']:
                item['status']='restart_backoff'
            else:
                # Reinspect immediately before launch to avoid a raced external
                # start. Binding itself is the final exclusive ownership guard.
                fresh_listeners,fresh_processes=self.inspect()
                if any(p['LocalPort']==service.port for p in fresh_listeners) or any(matching(service,p,self.root) for p in fresh_processes):
                    item['status']='start_raced_adopt_next_check'
                else:
                    item['attempts']=attempts+[timestamp]
                    item['next_attempt']=timestamp+min(300,5*2**min(len(attempts),6))
                    try: item.update(status='starting',pid=self.launch(service))
                    except (OSError,RuntimeError): item['status']='start_failed'
            if previous.get('status')!=item['status'] or previous.get('pid')!=item.get('pid'):
                self.event(service.name,item['status'])
            self.state['services'][service.name]=item; result[service.name]=item
        self.state.update(updated=timestamp,supervisor_pid=os.getpid())
        atomic(self.folder/'state.json',self.state)
        return all(item['healthy'] for item in result.values())


@contextmanager
def exclusive(folder):
    import msvcrt
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/'supervisor.lock').open('a+b') as stream:
        if stream.tell()==0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        try: msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError: raise RuntimeError('A local service supervisor is already running.')
        try: yield
        finally: stream.seek(0); msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--config',type=Path,help='Private service configuration; defaults to training-work/local-services/config.json.')
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--once',action='store_true',help='Read-only adoption/readiness; never start a service.')
    modes.add_argument('--run',action='store_true',help='Continuously repair missing services; never kill live processes.')
    parser.add_argument('--interval',type=float,default=10)
    args=parser.parse_args()
    if os.name!='nt': parser.error('The local service supervisor requires Windows.')
    if not 2<=args.interval<=60: parser.error('Interval must be 2–60 seconds.')
    try:
        with exclusive(args.root.resolve()/'training-work/local-services'):
            supervisor=Supervisor(args.root,args.config or args.root/'training-work/local-services/config.json')
            while True:
                try: healthy=supervisor.cycle(repair=args.run)
                except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired):
                    supervisor.event('supervisor','inspection_failed'); healthy=False
                if args.once:
                    print(json.dumps({'healthy':healthy,'services':{k:v['status'] for k,v in supervisor.state['services'].items()}}))
                    return 0 if healthy else 1
                time.sleep(args.interval)
    except RuntimeError:
        return 0  # A Task Scheduler repeat or a manual duplicate is harmless.


if __name__=='__main__': sys.exit(main())
