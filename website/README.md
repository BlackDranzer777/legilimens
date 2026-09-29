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

The buttons point to `https://github.com/BlackDranzer777/legilimens/releases/latest` by default.
When a GitHub Release is published, `js/main.js` reads the latest release from the GitHub API and
switches the buttons to direct asset links. It also fills in the version, file sizes and release date.

It matches the release assets by name:

| Button | Asset it looks for |
|---|---|
| Windows installer | `*Setup*.exe` (e.g. `Legilimens-Setup-1.0.0.exe` from `npm --prefix desktop run dist`) |
| Portable | any `*.zip` (e.g. a zip of `desktop/release/win-unpacked/`) |

If the API is unreachable or rate-limited, the buttons stay on the releases page, so they always work.
To change the repository, edit `REPO` at the top of `js/main.js` and the GitHub URLs in `index.html`.

## Deploy

Upload the `website/` folder to any static host. Examples: GitHub Pages (serve from `/website` with a
Pages workflow, or copy it to a `gh-pages` branch), Netlify or Cloudflare Pages (publish directory
`website`, no build command), or Vercel (root directory `website`).
