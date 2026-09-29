// Legilimens download site — progressive enhancement only.
// The page is fully usable without JS: download buttons fall back to the
// GitHub releases page and every feature description is in the HTML.

const REPO = 'BlackDranzer777/legilimens';

document.documentElement.classList.add('js');

/* ── Mobile nav ─────────────────────────────── */
const toggle = document.querySelector('.nav__toggle');
const links = document.getElementById('nav-links');
if (toggle && links) {
  const setOpen = (open) => {
    toggle.setAttribute('aria-expanded', String(open));
    links.classList.toggle('is-open', open);
  };
  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  links.addEventListener('click', (e) => { if (e.target.closest('a')) setOpen(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
}

/* ── Hero workspace carousel ────────────────── */
const app = document.querySelector('.app');
const views = [...document.querySelectorAll('.app__view')];
const thumbs = [...document.querySelectorAll('.thumb')];
const icons = [...document.querySelectorAll('.app__ico[data-for]')];
const order = thumbs.map((t) => t.dataset.target);
let current = 0;

function show(index, focus = false) {
  current = (index + order.length) % order.length;
  const name = order[current];
  views.forEach((v) => { v.hidden = v.dataset.view !== name; });
  thumbs.forEach((t, i) => {
    const on = i === current;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
    if (on && focus) t.focus();
  });
  icons.forEach((ic) => ic.classList.toggle('is-on', ic.dataset.for === name));
  if (app) app.setAttribute('aria-label', `Legilimens ${name} view`);
}

thumbs.forEach((t, i) => {
  t.addEventListener('click', () => show(i));
  t.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); show(current + 1, true); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); show(current - 1, true); }
  });
});
document.querySelectorAll('.thumbs__arrow').forEach((b) => {
  b.addEventListener('click', () => show(current + Number(b.dataset.step)));
});
if (thumbs.length) show(0);

/* ── Toolkit accordion (one open at a time) ──── */
const toolButtons = [...document.querySelectorAll('.tool__btn')];
toolButtons.forEach((btn) => {
  btn.addEventListener('click', () => {
    const opening = btn.getAttribute('aria-expanded') !== 'true';
    toolButtons.forEach((other) => {
      const open = other === btn && opening;
      other.setAttribute('aria-expanded', String(open));
      document.getElementById(other.getAttribute('aria-controls')).hidden = !open;
    });
  });
});

/* ── Reveal on scroll ───────────────────────── */
const revealTargets = document.querySelectorAll(
  '.why > *, .toolkit__aside, .tools, .download__head, .dl, .ethics'
);
if ('IntersectionObserver' in window) {
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-in');
        io.unobserve(entry.target);
      }
    });
  }, { rootMargin: '0px 0px -10% 0px' });
  revealTargets.forEach((el) => { el.classList.add('reveal'); io.observe(el); });
}

/* ── Newest release → download buttons ──────── */
// Buttons link straight to the release file, so the browser downloads it without
// leaving this page. The HTML ships a direct link to the current version; this
// swaps in the newest published installer (pre-releases included).
const RELEASES_PAGE = `https://github.com/${REPO}/releases`;
// Binary MB, matching what GitHub and browsers display for the file.
const formatSize = (bytes) => `${(bytes / 1048576).toFixed(0)} MB`;
const all = (key) => document.querySelectorAll(`[data-release="${key}"]`);
const setText = (key, text) => all(key).forEach((el) => { el.textContent = text; });
const setHref = (key, url) => all(key).forEach((a) => { a.href = url; });

async function loadRelease() {
  let releases;
  try {
    // /releases/latest skips pre-releases, so list them (newest first; drafts are never public).
    const res = await fetch(`https://api.github.com/repos/${REPO}/releases?per_page=10`, {
      headers: { Accept: 'application/vnd.github+json' },
    });
    if (res.status === 403 || res.status === 429) return; // rate-limited: keep the built-in direct link
    releases = res.ok ? await res.json() : []; // 404 = private repo: same as "nothing published"
  } catch {
    return; // offline: keep the built-in direct link
  }

  const isInstaller = (a) => /\.exe$/i.test(a.name);
  const release = releases.find((r) => !r.draft && (r.assets || []).some(isInstaller));
  if (!release) {
    // Nothing downloadable is published, so a direct link would hit GitHub's 404 page.
    setHref('installer', RELEASES_PAGE);
    return;
  }

  const assets = release.assets;
  const installer = assets.find((a) => /setup.*\.exe$/i.test(a.name)) || assets.find(isInstaller);
  const portable = assets.find((a) => /\.zip$/i.test(a.name));
  setText('version', release.tag_name.startsWith('v') ? release.tag_name : `v${release.tag_name}`);
  setHref('installer', installer.browser_download_url);
  setText('installer-size', formatSize(installer.size));
  if (portable) {
    setHref('portable', portable.browser_download_url);
    setText('portable-size', formatSize(portable.size));
    all('portable-card').forEach((card) => { card.hidden = false; });
  }
  if (release.published_at) {
    const date = new Date(release.published_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    setText('date', `Released ${date}`);
  }
}
loadRelease();

// Downloads start quietly in the browser's download bar, so point there. A click
// cannot confirm the download succeeded, so the message only says it was requested.
all('installer').forEach((link) => link.addEventListener('click', () => {
  if (!link.href.includes('/releases/download/')) return; // fallback link to the releases page
  setText('status', "Download requested. Check your browser's downloads.");
}));

/* ── /download (a Netlify rewrite of this page) lands on the download section ── */
if (location.pathname.replace(/\/+$/, '') === '/download') {
  document.getElementById('download')?.scrollIntoView({ behavior: 'instant' });
}

/* ── Footer year ────────────────────────────── */
const year = document.getElementById('year');
if (year) year.textContent = String(new Date().getFullYear());
