import { cp, mkdir, readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
execFileSync(process.execPath, ['--check', 'frontend/app.js'], { stdio: 'inherit' });
for (const name of ['index.html', 'style.css', 'app.js']) {
  if (!(await readFile(`frontend/${name}`, 'utf8')).trim()) throw new Error(`${name} が空です`);
}
await mkdir('dist', { recursive: true });
await cp('frontend', 'dist', { recursive: true });
console.log('画面の構文検証と dist/ へのビルドが完了しました。Python API と組み合わせて使用してください。');
