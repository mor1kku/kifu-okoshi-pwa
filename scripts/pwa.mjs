import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
const localPython = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
const python = process.env.PYTHON_EXECUTABLE || (existsSync(localPython) ? localPython : 'python');
const action = process.argv[2];
if (!['prepare', 'build'].includes(action)) throw new Error('prepare または build を指定してください。');
if (action === 'build') {
  for (const file of ['frontend/app.js', 'pwa/worker.js', 'pwa/local-engine.js', 'pwa/storage.js', 'pwa/bootstrap.js', 'pwa/sw.js']) {
    execFileSync(process.execPath, ['--check', file], { stdio: 'inherit' });
  }
}
execFileSync(python, [`scripts/${action}_pwa.py`], { stdio: 'inherit' });
