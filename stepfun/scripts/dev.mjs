// Local dev runner: starts the backend (port 3001) and the Vite dev server (port 5173).
// Usage: npm start   (after: npm run install:all)
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const isWin = process.platform === 'win32';
const npm = isWin ? 'npm.cmd' : 'npm';

const children = [];

function run(name, cmd, args, cwd) {
  const child = spawn(cmd, args, { cwd, shell: isWin, stdio: 'inherit' });
  child.on('exit', (code) => {
    console.log(`[dev] ${name} exited with code ${code}`);
    shutdown(code ?? 0);
  });
  children.push(child);
  return child;
}

function shutdown(code) {
  for (const c of children) {
    if (!c.killed) c.kill('SIGTERM');
  }
  process.exit(code);
}

process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

console.log('[dev] starting backend on :3001 ...');
run('backend', process.execPath, ['server.js'], path.join(root, 'backend'));

console.log('[dev] starting frontend on :5173 ...');
run('frontend', npm, ['run', 'dev'], path.join(root, 'frontend'));
