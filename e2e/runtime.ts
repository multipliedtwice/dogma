import {spawn, execFile, type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {join} from 'node:path';
import {clearTimeout as clearNodeTimeout, setTimeout as setNodeTimeout} from 'node:timers';
import {promisify} from 'node:util';
import type {APIRequestContext} from '@playwright/test';
import type {Book, ExportFile, Generation, Health, LibraryBook, Settings, Visual} from '../src/lib/types';
import type {IllustrationRun} from '../src/lib/illustrationRuns';
import {root, python, fixtureEnvironment} from './environment';

export type Operations = {
  library: {data: Record<string, never>; result: LibraryBook[]};
  book: {data: {id: string}; result: Book};
  generation: {data: {id: string}; result: Generation};
  settings: {data: Record<string, never>; result: Settings};
  health: {data: Record<string, never>; result: Health};
  visuals: {data: {book_id: string}; result: Visual[]};
  illustration_history: {data: {book_id: string}; result: IllustrationRun[]};
  illustration_status: {data: {id: string}; result: {id: string; status: string; error: string; complete: number; total: number}};
  export: {data: {id: string; format: 'srt' | 'vtt' | 'mp3' | 'm4b' | 'chapters'; chapters?: string[]}; result: ExportFile};
};
export type Api = <K extends keyof Operations>(op: K, data: Operations[K]['data']) => Promise<Operations[K]['result']>;
export type Control =
  | {op: 'hold-render' | 'hold-illustration'; after?: number}
  | {op: 'delete-audio'; generation_id: string; segment_id: string}
  | {op: 'check-rendered'; rendered?: string[]; unrendered?: string[]}
  | {op: 'release-render' | 'release-illustration' | 'wait-render-held' | 'wait-illustration-held' | 'fail-next-illustration'};

type Session = {port: number; token: string};
type Exit = {code: number | null; signal: NodeJS.Signals | null};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((accept, refuse) => {resolve = accept; reject = refuse;});
  return {promise, resolve, reject};
}

function startup(line: string): Session {
  const value: unknown = JSON.parse(line);
  if (value === null || typeof value !== 'object' || !('port' in value) || !('token' in value)
    || typeof value.port !== 'number' || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535
    || typeof value.token !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(value.token)) {
    throw new Error('Fixture startup must contain a valid port and session token.');
  }
  return {port: value.port, token: value.token};
}

function killGroup(child: ChildProcessWithoutNullStreams) {
  if (!child.pid) return;
  try {
    if (process.platform === 'win32') child.kill('SIGKILL');
    else process.kill(-child.pid, 'SIGKILL');
  } catch (error) {
    if (!(error && typeof error === 'object' && 'code' in error && error.code === 'ESRCH')) throw error;
  }
}

async function deadline<T>(promise: Promise<T>, milliseconds: number): Promise<T | undefined> {
  let timer: number | NodeJS.Timeout | undefined;
  try {
    return await Promise.race([promise, new Promise<undefined>(resolveTimeout => {timer = setNodeTimeout(resolveTimeout, milliseconds);})]);
  } finally {clearNodeTimeout(timer);}
}

async function launch(directory: string, request: APIRequestContext) {
  const child = spawn(python, ['-u', join(root, 'e2e/runtime/serve_fixture.py'), '--data-dir', join(directory, 'library'), '--static', join(root, 'dist')], {
    cwd: root, env: fixtureEnvironment(), stdio: ['pipe', 'pipe', 'pipe'], detached: process.platform !== 'win32',
  });
  const ready = deferred<Session>();
  const closed = deferred<Exit>();
  const pending = new Map<number, ReturnType<typeof deferred<void>>>();
  let stderr = '';
  let startupSeen = false;
  let exited = false;
  let fatal: Error | undefined;
  let stopRequested = false;
  let stopping: Promise<void> | undefined;
  let sequence = 0;
  const diagnostics = () => stderr ? `\nFixture stderr:\n${stderr}` : '\nFixture produced no stderr.';
  const fail = (error: Error) => {
    fatal ??= error;
    ready.reject(error);
    for (const reply of pending.values()) reply.reject(error);
    pending.clear();
  };
  const killAtExit = () => killGroup(child);
  process.once('exit', killAtExit);
  const stdoutLines = createInterface({input: child.stdout});
  const stderrLines = createInterface({input: child.stderr});
  stdoutLines.on('line', line => {
    try {
      if (startupSeen) throw new Error('Fixture wrote more than one startup line.');
      ready.resolve(startup(line));
      startupSeen = true;
    } catch (error) {
      fail(new Error(`Invalid fixture startup output: ${error instanceof Error ? error.message : String(error)}`));
      child.stdin.end();
    }
  });
  stderrLines.on('line', line => {
    if (line.startsWith('RIMSKIFY_E2E_CONTROL ')) {
      if (stopRequested) return;
      try {
        const value: unknown = JSON.parse(line.slice('RIMSKIFY_E2E_CONTROL '.length));
        if (value === null || typeof value !== 'object' || !('id' in value) || typeof value.id !== 'number' || !('ok' in value) || typeof value.ok !== 'boolean') throw new Error('Invalid control acknowledgement.');
        const reply = pending.get(value.id);
        if (!reply) throw new Error('Unexpected control acknowledgement.');
        pending.delete(value.id);
        if (value.ok) reply.resolve();
        else reply.reject(new Error('error' in value ? String(value.error) : 'Fixture control failed.'));
      } catch (error) {fail(new Error(`Fixture control protocol failed: ${String(error)}`));}
    } else {
      stderr = (stderr + line + '\n').slice(-32_768);
      process.stderr.write(`[fixture] ${line}\n`);
    }
  });
  child.once('error', error => fail(new Error(`Could not start E2E_PYTHON=${python}: ${error.message}`)));
  child.stdin.on('error', error => {if (!stopRequested) fail(new Error(`Fixture stdin failed: ${error.message}`));});
  child.once('close', (code, signal) => {
    exited = true;
    closed.resolve({code, signal});
    if (!stopRequested) fail(new Error(`Fixture exited (code ${code}, signal ${signal}).${diagnostics()}`));
  });

  async function stop() {
    if (!stopping) stopping = (async () => {
      stopRequested = true;
      for (const reply of pending.values()) reply.reject(new Error('Fixture is stopping.'));
      pending.clear();
      try {
        if (!exited) {
          child.stdin.end();
          if (process.platform !== 'win32') child.kill('SIGTERM');
        }
        let result = await deadline(closed.promise, 8_000);
        if (!result) {
          if (process.platform === 'win32' && child.pid) {
            await promisify(execFile)('taskkill', ['/PID', String(child.pid), '/T', '/F'], {timeout: 5_000}).catch(() => killGroup(child));
          } else killGroup(child);
          result = await deadline(closed.promise, 5_000);
          if (!result) throw new Error(`Could not reap fixture PID ${child.pid}.${diagnostics()}`);
          throw new Error(`Fixture required forced shutdown; owned process tree was killed and reaped.${diagnostics()}`);
        }
        if (fatal && startupSeen) throw new Error(`Fixture protocol/process failed: ${fatal.message}${diagnostics()}`, {cause: fatal});
        if (result.code !== 0 && startupSeen) throw new Error(`Fixture closed abnormally (${result.code}, ${result.signal}).${diagnostics()}`);
      } finally {
        process.removeListener('exit', killAtExit);
        stdoutLines.close();
        stderrLines.close();
      }
    })();
    return stopping;
  }

  let session: Session;
  try {
    const result = await deadline(ready.promise, 20_000);
    if (!result) throw new Error(`Fixture startup timed out.${diagnostics()}`);
    session = result;
  } catch (error) {
    await stop().catch(cleanup => {throw new AggregateError([error, cleanup], 'Fixture startup and cleanup failed.');});
    throw new Error(`Fixture startup failed.${diagnostics()}`, {cause: error});
  }
  const origin = `http://127.0.0.1:${session.port}`;

  const api: Api = async (op, data) => {
    if (fatal) throw new Error(`Fixture failed: ${fatal.message}${diagnostics()}`, {cause: fatal});
    if (exited || stopping) throw new Error(`Fixture is not running.${diagnostics()}`);
    const response = await request.post(`${origin}/api`, {headers: {Authorization: `Bearer ${session.token}`}, data: {op, data}, timeout: 15_000});
    if (!response.ok()) throw new Error(`${op}: HTTP ${response.status()}: ${await response.text()}${diagnostics()}`);
    return response.json();
  };
  async function control(command: Control) {
    if (fatal) throw new Error(`Fixture failed: ${fatal.message}${diagnostics()}`, {cause: fatal});
    if (exited || stopping) throw new Error(`Fixture is not running.${diagnostics()}`);
    const id = ++sequence;
    const reply = deferred<void>();
    pending.set(id, reply);
    const {op, ...data} = command;
    const timer = setTimeout(() => {pending.delete(id); reply.reject(new Error(`${op}: fixture control timed out.${diagnostics()}`));}, 15_000);
    try {
      child.stdin.write(JSON.stringify({id, op, data}) + '\n', error => {if (error) reply.reject(error);});
      await reply.promise;
    } finally {clearTimeout(timer); pending.delete(id);}
  }
  return {api, control, stop, origin, url: `${origin}/#port=${session.port}&token=${session.token}`, pid: child.pid};
}

export async function createRuntime(directory: string, request: APIRequestContext) {
  let instance = await launch(directory, request);
  const api: Api = (op, data) => instance.api(op, data);
  return {
    api,
    control: (command: Control) => instance.control(command),
    get origin() {return instance.origin;},
    get url() {return instance.url;},
    get pid() {return instance.pid;},
    async restart() {await instance.stop(); instance = await launch(directory, request);},
    stop: () => instance.stop(),
  };
}

export type Runtime = Awaited<ReturnType<typeof createRuntime>>;
