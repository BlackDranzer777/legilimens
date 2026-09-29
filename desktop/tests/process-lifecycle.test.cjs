const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { stopChild, forceKillTree, waitForExit } = require('../process-lifecycle.cjs');

function child() {
  return Object.assign(new EventEmitter(), { pid: 12345, exitCode: null, signalCode: null });
}
function exit(p) { p.exitCode = 0; p.emit('exit', 0); }
const tick = () => new Promise(setImmediate);

test('exit during shutdown HTTP response does not miss the event or force kill', async () => {
  const p = child();
  await stopChild(p, async () => exit(p), { force: () => assert.fail('unexpected kill') });
  assert.equal(p.listenerCount('exit'), 0);
});

test('concurrent stop calls share one shutdown and await forced cleanup', async () => {
  const p = child();
  let requests = 0;
  let release;
  let finished = false;
  const options = { grace: 1, force: async () => {
    await new Promise(resolve => { release = resolve; });
    exit(p);
  } };
  const a = stopChild(p, async () => { requests++; }, options);
  const b = stopChild(p, async () => { requests++; }, options);
  assert.equal(a, b);
  a.then(() => { finished = true; });
  while (!release) await tick();
  assert.equal(finished, false);
  release();
  await a;
  assert.equal(requests, 1);
  assert.equal(p.listenerCount('exit'), 0);
});

test('Windows kill waits for taskkill callback AND owned child exit', async () => {
  const p = child();
  let callback;
  let finished = false;
  const result = forceKillTree(p, { platform: 'win32', timeout: 1000,
    run: (cmd, args, options, cb) => {
      assert.equal(cmd, 'taskkill');
      assert.deepEqual(args, ['/PID', '12345', '/T', '/F']);
      assert.equal(options.windowsHide, true);
      assert.equal(options.timeout, 1000);
      callback = cb;
    } });
  result.then(() => { finished = true; });
  await tick();
  assert.equal(finished, false);
  callback(null);
  await tick();
  assert.equal(finished, false);
  exit(p);
  await result;
});

test('kill failure and missing exit are surfaced, not reported as success', async () => {
  await assert.rejects(forceKillTree(child(), { platform: 'win32',
    run: (_cmd, _args, _options, cb) => cb(new Error('access denied')) }), /access denied/);
  const p = child();
  await assert.rejects(forceKillTree(p, { platform: 'win32', timeout: 5,
    run: (_cmd, _args, _options, cb) => cb(null) }), /did not exit/);
  assert.equal(p.listenerCount('exit'), 0);
});

test('already-exited children are never killed and timeout listeners are removed', async () => {
  const p = child();
  assert.equal(await waitForExit(p, 1), false);
  assert.equal(p.listenerCount('exit'), 0);
  p.signalCode = 'SIGTERM';
  await forceKillTree(p, { run: () => assert.fail('must not target an exited PID') });
});

test('real owned process exits before forced-cleanup promise resolves', async () => {
  const p = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'],
    { windowsHide: true, stdio: 'ignore' });
  try {
    await once(p, 'spawn');
    await forceKillTree(p);
    assert.ok(p.exitCode !== null || p.signalCode !== null);
  } finally {
    if (p.exitCode === null && p.signalCode === null) {
      p.kill();
      await once(p, 'exit');
    }
  }
});
