# Legilimens website

The public download page for Legilimens: a static site (plain HTML, CSS and JS) with no build step and no dependencies.

```
website/
  index.html        the whole page
  css/styles.css    design tokens at the top (cream / ink / orange)
  js/main.js        carousel, feature list, mobile menu, release lookup
  assets/           favicon
```

## Preview locally

```bash
.venv/Scripts/python.exe -m http.server 5500 --bind 127.0.0.1 --directory website
```

Then open http://127.0.0.1:5500. Any static server works.

## Download links

The download button links **directly to the release file** on GitHub, so clicking it starts the
download immediately and the visitor stays on this page (GitHub serves release files as attachments).

`index.html` ships a direct link to the current version. On load, `js/main.js` lists the repo's
releases from the GitHub API (pre-releases included) and points the button at the newest published
installer, filling in its version, size and date. New releases therefore need no website change.

It matches the release assets by name:

| Button | Asset it looks for |
|---|---|
| Windows installer | `*Setup*.exe` (e.g. `Legilimens-Setup-1.0.0-preview.1.exe` from `scripts/build-windows.mjs`) |
| Portable | any `*.zip`; the card stays hidden unless the release has one |

- **No release published yet, or the repo is private:** a direct link would land on GitHub's 404
  page, so the button falls back to the releases page.
- **API unreachable or rate-limited:** the built-in direct link from `index.html` stays.

Keep the repository public: release files of a private repo are not downloadable by visitors.
When you publish a new version, also update the fallback link and version in `index.html`.
To change the repository, edit `REPO` at the top of `js/main.js` and the GitHub URLs in `index.html`.

## Deploy

Upload the `website/` folder to any static host. Examples: GitHub Pages (serve from `/website` with a
Pages workflow, or copy it to a `gh-pages` branch), Netlify or Cloudflare Pages (publish directory
`website`, no build command), or Vercel (root directory `website`).
