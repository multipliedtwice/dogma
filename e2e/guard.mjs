import {execFile} from 'node:child_process';
import {readFile, statfs} from 'node:fs/promises';
import {createServer} from 'node:net';
import {cpus, freemem, loadavg, platform, tmpdir, totalmem} from 'node:os';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';

const execute = promisify(execFile);
const GiB = 1024 ** 3;
const limits = {freeMemory: GiB, freeDisk: 2 * GiB, cpuBusy: .90, loadPerCore: 1.5};

export function parseProcesses(text) {
  return text.split('\n').flatMap(line => {
    const match = /^\s*(\d+)\s+(\d+)\s+([\d.]+)\s+(.+)$/.exec(line);
    return match ? [{pid: Number(match[1]), ppid: Number(match[2]), command: match[4]}] : [];
  });
}

export function ancestors(rows, pid) {
  const parents = new Map(rows.map(row => [row.pid, row.ppid]));
  const result = new Set();
  while (pid > 0 && !result.has(pid)) {
    result.add(pid);
    pid = parents.get(pid) || 0;
  }
  return result;
}

export function competingProcesses(rows, pid) {
  const own = ancestors(rows, pid);
  const target = /(?:^|[\s/\\"'])(?:@?playwright(?:-core)?(?:\.cmd)?|serve_fixture\.py|browser_[\w]+\.py|run-e2e(?:\.[\w-]+)?|cypress(?:\.cmd)?)(?=$|[\s/\\"'])/i;
  const npmE2E = /\b(?:npm|pnpm|yarn|bun)(?:\.cmd)?\s+(?:run\s+)?e2e(?=$|[:\s])/i;
  const build = /(?:^|[\s/\\])(?:vite(?:\.js)?\s+build|tsc(?:\.js)?(?:\s|$)|pytest(?:\s|$)|python[\d.]*\s+-m\s+unittest)/i;
  return rows.filter(row => !own.has(row.pid) && (target.test(row.command) || npmE2E.test(row.command) || build.test(row.command)));
}

export function assessResources(value) {
  const problems = [];
  if (value.cpuBusy >= limits.cpuBusy) problems.push(`CPU busy ${Math.round(value.cpuBusy * 100)}%. Stop competing work, then retry.`);
  if (value.load / value.cores > limits.loadPerCore) problems.push(`CPU load ${value.load.toFixed(1)} across ${value.cores} available cores. Wait for load to settle.`);
  if (value.availableMemory < limits.freeMemory || value.availableMemory / value.totalMemory < .08) problems.push('Less than 1 GiB or 8% usable memory. Close memory-heavy apps, then retry.');
  if (value.memoryPressure) problems.push('Memory pressure is high. Close memory-heavy apps, then retry.');
  if (value.disks.some(disk => disk.free < limits.freeDisk)) problems.push('Less than 2 GiB free on the checkout or temporary disk. Free space before running.');
  return problems;
}

async function optionalText(path) {
  try { return (await readFile(path, 'utf8')).trim(); }
  catch (error) {
    if (error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT') return '';
    throw error;
  }
}

async function processSnapshot() {
  if (platform() === 'win32') {
    const {stdout} = await execute('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', 'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress'], {timeout: 10000, maxBuffer: 8 * 1024 ** 2});
    const decoded = JSON.parse(stdout);
    const rows = (Array.isArray(decoded) ? decoded : [decoded]).map(row => ({pid: row.ProcessId, ppid: row.ParentProcessId, command: row.CommandLine || ''}));
    if (!rows.some(row => row.pid === process.pid)) throw new Error('Cannot inspect own process ancestry. Check process-list permissions.');
    return rows;
  }
  const {stdout} = await execute('ps', ['-axo', 'pid=,ppid=,pcpu=,args='], {timeout: 5000, maxBuffer: 8 * 1024 ** 2});
  const rows = parseProcesses(stdout);
  if (!rows.some(row => row.pid === process.pid)) throw new Error('Cannot inspect process ancestry. Install/allow ps before running.');
  return rows;
}

function cpuTimes() {
  return cpus().reduce((total, cpu) => ({idle: total.idle + cpu.times.idle, all: total.all + Object.values(cpu.times).reduce((a, b) => a + b, 0)}), {idle: 0, all: 0});
}

async function memoryAndCores() {
  let availableMemory = freemem();
  let totalMemory = totalmem();
  let cores = Math.max(1, cpus().length);
  let memoryPressure = false;
  if (platform() === 'linux') {
    const meminfo = await readFile('/proc/meminfo', 'utf8');
    const match = /^MemAvailable:\s+(\d+)\s+kB/m.exec(meminfo);
    if (!match) throw new Error('Cannot read MemAvailable from /proc/meminfo. Check permissions.');
    availableMemory = Number(match[1]) * 1024;
    const maximum = await optionalText('/sys/fs/cgroup/memory.max') || await optionalText('/sys/fs/cgroup/memory/memory.limit_in_bytes');
    const current = await optionalText('/sys/fs/cgroup/memory.current') || await optionalText('/sys/fs/cgroup/memory/memory.usage_in_bytes');
    if (/^\d+$/.test(maximum) && /^\d+$/.test(current) && Number(maximum) < totalMemory) {
      totalMemory = Number(maximum);
      availableMemory = Math.min(availableMemory, Math.max(0, totalMemory - Number(current)));
    }
    const quota = (await optionalText('/sys/fs/cgroup/cpu.max')).split(/\s+/).map(Number);
    if (quota[0] > 0 && quota[1] > 0) cores = Math.min(cores, quota[0] / quota[1]);
    const pressures = await Promise.all(['/proc/pressure/memory', '/sys/fs/cgroup/memory.pressure'].map(optionalText));
    memoryPressure = pressures.some(pressure => {
      const full = /^full avg10=([\d.]+)/m.exec(pressure);
      return Boolean(full && Number(full[1]) >= 5);
    });
  } else if (platform() === 'darwin') {
    const {stdout} = await execute('memory_pressure', ['-Q'], {timeout: 5000});
    const match = /System-wide memory free percentage:\s*(\d+)%/.exec(stdout);
    if (!match) throw new Error('Cannot query macOS memory pressure. Run memory_pressure -Q and check permissions.');
    availableMemory = totalMemory * Number(match[1]) / 100;
    memoryPressure = Number(match[1]) < 10;
  } else if (platform() !== 'win32') {
    throw new Error(`E2E guard does not support ${platform()}. Resource checks must not be bypassed.`);
  }
  return {availableMemory, totalMemory, cores, memoryPressure};
}

async function checkLoopback() {
  await new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once('error', error => reject(new Error(`Cannot bind 127.0.0.1 on port 0: ${error.message}. Allow local loopback sockets.`)));
    server.listen(0, '127.0.0.1', () => server.close(error => error ? reject(error) : resolvePort(undefined)));
  });
}

export async function guard() {
  const before = cpuTimes();
  const [rows, memory, disks] = await Promise.all([
    processSnapshot(), memoryAndCores(),
    Promise.all([process.cwd(), tmpdir()].map(async path => {
      const disk = await statfs(path);
      return {path, free: disk.bavail * disk.bsize};
    })),
    new Promise(done => setTimeout(done, 300)),
  ]);
  const after = cpuTimes();
  const total = after.all - before.all;
  if (total <= 0) throw new Error('Cannot sample CPU activity. Retry after checking system resource access.');
  const problems = assessResources({...memory, disks, cpuBusy: 1 - (after.idle - before.idle) / total, load: loadavg()[0]});
  const competing = competingProcesses(rows, process.pid);
  for (const row of competing) problems.push(`Competing test/build process PID ${row.pid}: ${row.command.slice(0, 180)}. Stop it yourself, then retry.`);
  if (problems.length) throw new Error(problems.join('\n'));
  await checkLoopback();
  console.log('E2E guard: safe. Ephemeral loopback available. No fixed ports, containers or external services required.');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  guard().catch(error => {console.error(`E2E guard refused:\n${error.message}`); process.exitCode = 1;});
}
