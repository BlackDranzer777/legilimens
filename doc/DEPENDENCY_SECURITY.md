# Dependency Security

Reviewed 2026-09-25. This is a point-in-time advisory check and compatibility pass,
not evidence that the application or its dependencies are vulnerability-free.
Existing frozen executables/installers are unchanged and must not be treated as patched.

## Scope and Results

| Scope | Initial findings | Final advisory result |
| --- | --- | --- |
| Root npm lock | 1 affected package, critical (`tar` in an obsolete dependency tree) | 0 |
| Frontend npm lock | 7 affected packages: 4 high, 2 moderate, 1 low | 0 |
| Desktop npm lock, including dev/build dependencies | 10 affected packages: 1 critical, 9 high | 0 |
| Legacy Node server npm lock | 4 affected packages, moderate | 0 |
| Installed Python environment | 31 advisory records across AnyIO, cryptography, Pillow, pip | No known vulnerabilities found |
| Resolved Python runtime and development locks | Newly added | No known vulnerabilities found in either lock |

Npm counts are affected dependency entries, not distinct CVEs. Python advisory
records can have duplicate CVE/GHSA/PYSEC aliases. No advisories were suppressed and
development dependencies were included. Reachability/exploitability of each advisory
was not established; several findings were in build tools or documentation tooling.

## Changes

- Vite 4.5.14 -> 6.4.3, React plugin 4.7.0, PostCSS 8.5.28, and compatible patched
  transitives. Vite 6.4 is currently a security-maintained branch according to the
  [Vite support policy](https://vite.dev/releases); this avoids an unnecessary bundler migration.
- Electron 31 -> 44.4.5 and electron-builder 24 -> 26.15.3. The current Electron
  package also replaces the vulnerable `extract-zip` installer dependency. Electron's
  [support policy](https://www.electronjs.org/docs/latest/tutorial/electron-timelines)
  covers the latest three stable majors, not the old 31 line.
- Removed unused `file:..` self-dependencies from client/server and reconciled the
  root lockfile with its dependency-free launcher manifest. The old native Node
  backend is reference code, not part of the Python application runtime.
- Reference-server UUID -> 11.1.1; refreshed Express/body-parser/qs transitives within
  compatible ranges. This is a dependency audit, not validation of that legacy server.
- Runtime lock includes cryptography 50.0.0, pyOpenSSL 26.4.0, AnyIO 4.15.1, and
  Starlette 1.7.0. aioquic remains 1.3.0 because resource budgets inspect its internals;
  the reviewed direct application baseline is in `python/requirements.in`.
- Added complete hash-locked runtime and development/packaging requirements. The
  development input is constrained by the runtime lock so those baselines agree.
  Pip is pinned to 26.2.1 in development tools. The existing documentation environment
  received Pillow 12.3.0; its minimum is also recorded in `doc/requirements.txt`.
- Added repeatable npm audit scripts, `.nvmrc` (24), and local Vite/Electron smoke tests.

## Verification

Validated on Windows with CPython 3.12 and Node 24.19.0. The installed system Node
20.17.0 was not replaced. Electron development needs Node >=22.12; use a maintained
Node 24 patch release for the documented workflow. Engine compatibility alone is
not a Node security audit. The Electron smoke reported Electron 44.4.5, Chromium
152.0.7977.130, and embedded Node 24.21.0.

- A fresh temporary environment installed all 29 runtime packages using the locked
  hashes and passed `pip check`. Its final backend run discovered 99 tests:
  98 passed and one opt-in soak was skipped. The temporary environment was removed.
- 21 frontend tests and 11 launcher tests passed under Node 24.
- TypeScript/Vite production build passed; the temporary Vite loopback smoke served
  HTML and transformed TSX successfully. Stopping the smoke can cancel background optimization.
- A hidden, unpackaged Electron window loaded a temporary loopback page, verified
  secure-context WebTransport availability, and verified `require`/`process` were
  unavailable in the renderer with sandbox/context isolation enabled.
- electron-builder CLI 26.15.3 loaded successfully. No installer or application bundle was built.
- Both hash-locked requirement files installed successfully; `pip check` passed.
- All four npm audits, both requirement-file audits, and the full installed Python
  environment audit reported no known findings at the final check.

An earlier backend test run overlapped dependency installation and failed with a
mixed-version AnyIO import. It is not counted as a pass; the subsequent complete
run after installation passed. Do not install dependencies while tests or the
backend are running.

The first fresh-environment run also exposed a pre-existing certificate-lock race:
initialization wrote a byte before acquiring the Windows lock, occasionally failing
when another thread already held it (and leaking the failed opener's handle).
Removed that unnecessary write; Windows supports byte-range locking past EOF.
A new no-write regression, 20 consecutive concurrency runs, and the subsequent
fresh-environment full suite passed. This is a targeted lifecycle correction found
during dependency validation, not a vulnerability attributed to a dependency.

## Reproduce

Use Node 24 and activate the project virtual environment. Normal installs should
use `npm ci` and the checked-in Python hashes, not an unconstrained update.

```powershell
python -m pip install --require-hashes -r python/requirements-dev.txt
npm --prefix client ci
npm --prefix desktop ci
npm run audit:js
npm run audit:python
python -m pip_audit
python -m pip check
python -m unittest discover -s python/tests -v
npm --prefix client test
npm --prefix client run build
npm run test:launchers
npm run test:vite
npm --prefix desktop run test:runtime
```

To intentionally refresh Python dependencies, edit the `.in` files, compile runtime
first, then compile development with the runtime constraint, install, audit, and test:

```powershell
python -m piptools compile --generate-hashes --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file=python/requirements.txt python/requirements.in
python -m piptools compile --generate-hashes --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host --output-file=python/requirements-dev.txt python/requirements-dev.in
```

Locks were resolved on Windows/Python 3.12. Cross-platform resolution, source/native
builds, packaged Electron operation, clean-machine installation, and real-application
interoperability remain unconfirmed. The full long-running resource soak was not
rerun. Native libraries not separately represented by package advisories are not
independently audited. Re-run advisory checks before release and after dependency updates.
