const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const tick = () => new Promise(setImmediate);

function harness(env = {}) {
  let release, fail, calls = 0, quits = 0;
  const errors = [], profiles = [], directories = [];
  const app = Object.assign(new EventEmitter(), {
    setName() {}, whenReady: () => ({ then() {} }), quit: () => { quits++; },
    setPath: (...args) => profiles.push(args),
  });
  const context = vm.createContext({
    require: name => {
      if (name === 'electron') return { app, dialog: { showErrorBox: (...args) => errors.push(args) } };
      if (name === 'fs') return { ...fs, mkdirSync: (...args) => directories.push(args) };
      if (name === './process-lifecycle.cjs') return { stopChild: () => {
        calls++;
        return new Promise((resolve, reject) => { release = resolve; fail = reject; });
      } };
      return require(name);
    },
    __dirname: path.resolve(__dirname, '..'),
    process: { env, pid: 1, stdout: { write() {} }, stderr: { write() {} } },
    setTimeout, clearTimeout,
  });
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../main.cjs'), 'utf8'), context);
  return { app, errors, profiles, directories, release: () => release(), fail: () => fail(new Error('taskkill denied')),
    calls: () => calls, quits: () => quits };
}

test('explicit desktop profile is isolated while default profile remains unchanged', () => {
  assert.deepEqual(harness().profiles, []);
  const h = harness({ LEGILIMENS_DESKTOP_DATA_DIR: 'build/isolated-desktop' });
  assert.deepEqual(h.profiles, [['userData', path.resolve('build/isolated-desktop')]]);
  assert.equal(h.directories[0][0], path.resolve('build/isolated-desktop'));
  assert.equal(h.directories[0][1].recursive, true);
});

test('all Electron quit requests wait for the same backend cleanup', async () => {
  const h = harness();
  let prevented = 0;
  h.app.emit('before-quit', { preventDefault: () => prevented++ });
  h.app.emit('window-all-closed');
  h.app.emit('before-quit', { preventDefault: () => prevented++ });
  assert.equal(prevented, 2);
  assert.equal(h.calls(), 1);
  assert.equal(h.quits(), 0);
  h.release();
  await tick();
  assert.equal(h.quits(), 1);
  h.app.emit('before-quit', { preventDefault: () => assert.fail('cleanup already finished') });
});

test('Electron surfaces cleanup failure before quitting', async () => {
  const h = harness();
  h.app.emit('window-all-closed');
  h.fail();
  await tick();
  assert.equal(h.errors[0][0], 'Backend cleanup failed');
  assert.match(h.errors[0][1], /taskkill denied/);
  assert.equal(h.quits(), 1);
});
