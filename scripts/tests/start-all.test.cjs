const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(setImmediate);

function harness() {
  const children = [], exits = [], timers = new Map();
  let instance = 'abcdef01';
  function exit(child, code) { child.exitCode = code; child.emit('exit', code); }
  const spawn = (cmd, args, options) => {
    const child = Object.assign(new EventEmitter(), {
      cmd, args, options, pid: children.length + 100, exitCode: null, signalCode: null,
      stdout: new EventEmitter(), stderr: new EventEmitter(),
    });
    children.push(child);
    return child;
  };
  const http = { get: (_url, _options, callback) => {
    const response = new EventEmitter();
    response.statusCode = 200;
    queueMicrotask(() => {
      callback(response);
      response.emit('data', JSON.stringify({ service: 'legilimens', instanceId: instance }));
      response.emit('end');
    });
    return Object.assign(new EventEmitter(), { setTimeout() {}, destroy() {} });
  } };
  const filename = path.resolve(__dirname, '../start-all.js');
  const source = fs.readFileSync(filename, 'utf8')
    .replace(/^import .*$/gm, '').replace('import.meta.url', JSON.stringify(filename));
  const context = vm.createContext({
    spawn, path, fs: { existsSync: () => true }, http, fileURLToPath: s => s,
    randomBytes: () => Buffer.from('test-control-token'), console: { log() {} },
    processLifecycle: {
      forceKillTree: async p => exit(p, 0),
      stopChild: async p => { if (p && p.exitCode === null) exit(p, 0); },
    },
    process: { platform: 'win32', pid: 99, env: {}, on() {}, exit: code => exits.push(code) },
    setTimeout: (fn, ms) => { const id = {}; timers.set(id, { fn, ms }); return id; },
    clearTimeout: id => timers.delete(id),
  });
  vm.runInContext(source, context, { filename });
  async function ready(child, id = 'abcdef01') {
    instance = id;
    child.stdout.emit('data', Buffer.from('REA'));
    child.stdout.emit('data', Buffer.from(`DY instanceId=${id}\n`));
    await tick();
  }
  return { children, exits, timers, exit, ready };
}

test('rotation restarts backend, checks split READY and preserves Vite/token', async () => {
  const h = harness();
  await h.ready(h.children[0]);
  assert.equal(h.children[1].cmd, 'npm');
  const ui = h.children[1];
  h.exit(h.children[0], 75);
  assert.equal(h.children.length, 3);
  await h.ready(h.children[2], 'abcdef02');
  assert.equal(h.children.length, 3);
  assert.equal(ui.exitCode, null);
  assert.equal(h.children[2].options.env.LEGILIMENS_CONTROL_TOKEN,
    h.children[0].options.env.LEGILIMENS_CONTROL_TOKEN);
  assert.equal(h.timers.size, 0);
  assert.deepEqual(h.exits, []);
});

test('restart limit stops the app instead of looping', async () => {
  const h = harness();
  let backend = h.children[0];
  for (let i = 0; i < 4; i++) {
    await h.ready(backend);
    h.exit(backend, 75);
    backend = h.children.at(-1);
  }
  await tick();
  assert.equal(h.children.length, 5); // four backend instances, one UI
  assert.deepEqual(h.exits, [75]);
  assert.equal(h.children[1].exitCode, 0);
});

test('failed restart readiness stops siblings and exits nonzero', async () => {
  const h = harness();
  await h.ready(h.children[0]);
  h.exit(h.children[0], 75);
  const [{ fn }] = h.timers.values();
  fn();
  await tick();
  assert.deepEqual(h.exits, [1]);
  assert.equal(h.children[1].exitCode, 0);
  assert.equal(h.children[2].exitCode, 0);
});
