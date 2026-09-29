// Build a local unsigned preview. Never publishes or installs the result.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const desktop = path.join(root, 'desktop');
const python = path.join(root, '.venv/Scripts/python.exe');
if (process.platform !== 'win32') throw new Error('This build script targets Windows x64.');
const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || (major === 22 && minor < 12)) throw new Error('Use Node >=22.12 (Node 24 recommended).');
if (!fs.existsSync(python)) throw new Error('Missing .venv: install the locked Python build dependencies first.');
const config = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json')));
if (!config.version.includes('-preview.')) throw new Error('This script only creates preview versions.');
const env = { ...process.env, CSC_IDENTITY_AUTO_DISCOVERY: 'false' };
for (const key of ['CSC_LINK', 'WIN_CSC_LINK', 'CSC_KEY_PASSWORD', 'WIN_CSC_KEY_PASSWORD']) delete env[key];
function run(command, args, cwd = root) {
  const result = spawnSync(command, args, { cwd, env, stdio: 'inherit', windowsHide: true });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${path.basename(command)} failed (${result.status})`);
}
run(process.execPath, ['node_modules/typescript/bin/tsc'], path.join(root, 'client'));
run(process.execPath, ['node_modules/vite/bin/vite.js', 'build'], path.join(root, 'client'));
run(python, ['-m', 'PyInstaller', 'legilimens-backend.spec', '--noconfirm']);
run(process.execPath, ['node_modules/electron-builder/cli.js', '--win', '--x64', '--publish', 'never',
  '--config.electronDist=node_modules/electron/dist'], desktop);

const output = path.join(desktop, config.build.directories.output);
const files = [`Legilimens-Setup-${config.version}.exe`, 'win-unpacked/Legilimens.exe',
  'win-unpacked/resources/app.asar', 'win-unpacked/resources/backend/legilimens-backend.exe'];
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const manifest = {
  version: config.version, builtAt: new Date().toISOString(), node: process.versions.node,
  distribution: 'unsigned research preview', published: false,
  validation: 'Build only; run packaged smoke and clean-machine validation separately.',
  artifacts: files.map(file => ({ file, bytes: fs.statSync(path.join(output, file)).size,
    sha256: hash(path.join(output, file)) })),
};
fs.writeFileSync(path.join(output, 'build-manifest.json'), JSON.stringify(manifest, null, 2));
console.log(`Preview built: ${output}`);
