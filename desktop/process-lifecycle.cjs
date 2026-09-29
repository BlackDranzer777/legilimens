const { execFile } = require('node:child_process');

function exited(child) {
  return !child || !child.pid || child.exitCode !== null || child.signalCode !== null;
}

function waitForExit(child, timeout) {
  if (exited(child)) return Promise.resolve(true);
  return new Promise((resolve) => {
    const finish = (value) => {
      clearTimeout(timer);
      child.removeListener('exit', onExit);
      resolve(value);
    };
    const onExit = () => finish(true);
    const timer = setTimeout(() => finish(false), timeout);
    child.once('exit', onExit);
    if (exited(child)) finish(true);
  });
}

async function forceKillTree(child, { run = execFile, platform = process.platform, timeout = 5000 } = {}) {
  if (exited(child)) return;
  if (platform === 'win32') {
    await new Promise((resolve, reject) => {
      run('taskkill', ['/PID', String(child.pid), '/T', '/F'],
        { windowsHide: true, timeout }, (error) => {
          if (error && !exited(child)) reject(error);
          else resolve();
        });
    });
  } else {
    child.kill('SIGKILL');
  }
  if (!await waitForExit(child, timeout)) {
    throw new Error(`Owned process ${child.pid} did not exit after forced termination.`);
  }
}

const stops = new WeakMap();
function stopChild(child, requestShutdown, { grace = 8000, force = forceKillTree } = {}) {
  if (exited(child)) return Promise.resolve();
  if (stops.has(child)) return stops.get(child);
  const stopping = (async () => {
    await requestShutdown();
    // Recheck after the HTTP response: the exit event may already have arrived.
    if (!await waitForExit(child, grace)) await force(child);
  })();
  stops.set(child, stopping);
  return stopping;
}

module.exports = { exited, waitForExit, forceKillTree, stopChild };
