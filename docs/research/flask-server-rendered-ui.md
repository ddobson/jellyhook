# Research: server-rendered UI options for a Flask app

Ticket: JLYH-7 (parent map: JLYH-5). Researched 2026-11-21 against Flask 3.1.x docs, htmx 2.x docs and
the repository as of `main` at e9c4abd. Facts and trade-offs only; no deployment or dependency decision
is made here.

Sources used: Flask docs (flask.palletsprojects.com, "stable" = 3.1.x), htmx docs and essays
(htmx.org), and this repo's own files. Where a statement comes from the repo rather than a doc, the
file is named. Items I could not confirm from those sources are listed under "Not established".

## 1. What the API already has

From `api/pyproject.toml` and `api/uv.lock`:

- Direct dependencies: `flask>=3.1.0,<4`, `gunicorn>=23,<24`, `loguru`, `pika`.
- Transitive, already locked: `jinja2`, `markupsafe`, `werkzeug`, `itsdangerous`, `blinker`.
- The app is an application factory (`api/src/api/app.py`, `create_app`) that registers one blueprint,
  `webhooks`, at `/hooks` with a `before_request` bearer-token check (`api/src/api/auth.py`, constant-time
  `hmac.compare_digest`). `/health` is a plain route.
- `ProductionConfig.SECRET_KEY = os.getenv("SECRET_KEY")` (None when unset) and
  `DevelopmentConfig.SECRET_KEY = "dev"`. `docs/guides/docker.md` already passes `-e SECRET_KEY=<random>`
  to the API container.
- `MAX_CONTENT_LENGTH` is already 1 MiB (`api/src/api/config.py`).
- `api/src/api/errors.py` maps `HTTPException` to its own status; whether its responses are JSON-only
  was not checked (see "Not established").

## 2. What Flask ships with (no new packages)

Jinja templating
- "Flask leverages Jinja as its template engine"; Jinja must be installed to run Flask at all, so it is
  already a dependency. (Flask, Templates)
- Autoescaping is on for templates ending `.html`, `.htm`, `.xml`, `.xhtml`, and `.svg` via
  `render_template()`, and for all strings with `render_template_string()`. (Flask, Templates)
- `config`, `request`, `session`, `g`, `url_for()` and `get_flashed_messages()` are available in the
  template context. (Flask, Templates)
- Templates are looked up in a `templates` folder next to the module, or inside the package if the app is
  a package. (Flask, Quickstart)
- Template inheritance, filters (`@app.template_filter`) and context processors are built in.
  (Flask, Templates; Quickstart)

Blueprints
- A blueprint can carry its own `template_folder` and `static_folder`. Blueprint template folders are
  searched with lower priority than the application's folder. Name templates under a subfolder
  (for example `ui/index.html`) to avoid collisions. (Flask, Blueprints)
- A blueprint static folder is only reachable if the blueprint has a `url_prefix`; otherwise `/static`
  belongs to the application. (Flask, Blueprints)
- `before_request` registered on a blueprint (as `webhooks` already does) applies to that blueprint, so a
  UI blueprint can have its own login check without affecting `/hooks`. Blueprint 404/405 handlers only
  fire from `abort()`/`raise`, not for unmatched URLs; those must be handled at app level. (Flask,
  Blueprints)

Sessions and flash messages
- `session` is a cryptographically signed cookie; the user "could look at the contents of your cookie but
  not modify it, unless they know the secret key". A `SECRET_KEY` is required. (Flask, Quickstart)
- Server-side sessions are not built in; "there are several Flask extensions" for that. (Flask, Quickstart)
- `flash()` / `get_flashed_messages()` use the session; messages too large for the cookie "fail
  silently". (Flask, Patterns: Message Flashing)
- `SECRET_KEY_FALLBACKS` (added in 3.1) allows key rotation without invalidating sessions. (Flask, Config)

Session cookie settings (defaults from Flask, Config)
- `SESSION_COOKIE_HTTPONLY` default True.
- `SESSION_COOKIE_SECURE` default False ("the application must be served over HTTPS for this to make
  sense").
- `SESSION_COOKIE_SAMESITE` default None; Flask recommends `'Lax'` (or `'Strict'`).
- `PERMANENT_SESSION_LIFETIME` default 31 days, applies only when `session.permanent` is true; Flask
  validates the signature age against it. Without `permanent`, the cookie is removed when the browser
  closes. (Flask, Config; Security Considerations)
- Flask's example login clears then sets the session: `session.clear(); session[...] = ...;
  session.permanent = True`. (Flask, Security Considerations)

Forms
- `request.form` for POST bodies; a missing key raises a 400. `redirect()`, `url_for()` and `abort()` are
  built in. (Flask, Quickstart)
- Flask has no form validation framework. (Flask, Security Considerations: "the form validation
  framework, which does not exist in Flask")

## 3. CSRF protection

- Flask does not provide CSRF protection. Its docs describe the mechanism to implement: store a one-time
  token in the cookie (session) and also send it with the form data, then compare the two on the server
  for every state-changing request. (Flask, Security Considerations)
- Flask's documented mitigations for cookie-based auth include `SameSite` (`Lax` "prevents sending
  cookies with CSRF-prone requests from external sites, such as submitting a form"). (Flask, Security
  Considerations)
- htmx's security essay: "`SameSite=Lax` is usually enough to protect against CSRF" (written 2024) but
  "only protects you at the domain level, not the subdomain level". It recommends cookies with `Secure`,
  `HttpOnly`, `SameSite=Lax`. (htmx.org, Web Security Basics with htmx)
- htmx can send a CSRF token on every request via `hx-headers` on `<body>`/an ancestor or the
  `htmx:configRequest` event. htmx warns `hx-boost` does not update those tags, so put the token on an
  element that is replaced. (htmx.org, Docs)
- Hand-rolled option using only already-locked or stdlib code: generate with `secrets.token_urlsafe()`,
  keep it in `session`, render as a hidden field or `hx-headers`, compare with `hmac.compare_digest`
  (the repo already uses `hmac.compare_digest` in `api/auth.py`). This is a pattern derived from the Flask
  description above, not a snippet from the docs.
- Third-party packages that provide CSRF (for example Flask-WTF) are outside the sources allowed for this
  ticket, so their contents are not described here.

## 4. Shared-secret login without an auth framework

Facts from the sources:
- Session cookie plus `before_request` check is the Flask-native building block (see section 2).
- Flask says to "use `itsdangerous.TimedSerializer` to sign and validate other cookie values"
  (`itsdangerous` is already locked). (Flask, Security Considerations)
- The repo already has a constant-time shared-secret comparison for the webhook token
  (`api/src/api/auth.py`).
- htmx says to use cookies, not JavaScript-set `Authorization` headers, for browser auth, set with
  `Secure`, `HttpOnly`, `SameSite=Lax`. (htmx.org, Web Security Basics)

Trade-off notes (derived, not quoted):
- A login form that posts the secret and sets a flag in `session` needs no new package but needs its own
  CSRF token. Flask's own example calls `session.clear()` before setting values at login.
- The login secret should be a different value from `SECRET_KEY` (which signs cookies) and, if desired,
  from `WEBHOOK_TOKEN`; that is a design choice, not a documented requirement.
- No brute-force throttling is built into Flask. Flask's docs list resource-use limits only
  (`MAX_CONTENT_LENGTH`, `MAX_FORM_MEMORY_SIZE` 500 kB default, `MAX_FORM_PARTS` 1000 default).
- HTTP Basic auth is not covered in the sources reviewed.

## 5. htmx as the interactive layer

- htmx is "a dependency-free, browser-oriented javascript library" with "no need for a build system".
  Latest is 2.x (docs show 2.0.11); 1.x is still supported. (htmx.org, Docs)
- Install options in the docs: CDN script tag (docs advise "not using CDNs in production"), "copy it into
  your project" (vendor), or `npm install htmx.org`.
- htmx.org does not document a Python/PyPI package. Vendoring adds one static file and no Python
  dependency; the npm route would add a Node toolchain to the build. (derived)
- Serving a vendored file: put it under the app or blueprint `static` folder and reference with
  `url_for('static', filename=...)`. Flask says production static files are "ideally" served by the web
  server, but Flask can serve them itself. (Flask, Quickstart)
- Requests carry an `HX-Request: true` header, so a view can return a fragment for htmx and a full page
  otherwise. (htmx.org, Docs)
- htmx security settings: `selfRequestsOnly` (default true), `allowEval` can be set false (disables
  `hx-on:`, event filters, `js:` values), `hx-disable` blocks htmx processing inside an element,
  `historyCacheSize` can be 0 to avoid storing HTML in `localStorage`. (htmx.org, Docs)
- htmx's rule: escape all untrusted content injected into the page; with Jinja autoescape on, this holds
  for `.html` templates. Avoid `|safe` / `Markup` on user or Jellyfin-supplied data. (htmx.org Web
  Security Basics; Flask, Web Security)
- Everything in section 2 works without htmx; htmx is optional for partial updates (for example polling
  or filtering without full reloads).

## 6. Security headers and hardening Flask documents

All set by hand via `response.headers` (Flask also names the Flask-Talisman extension, not reviewed
here). (Flask, Security Considerations)
- `Content-Security-Policy: default-src 'self'` as a strict baseline; Flask says CSP is the way to stop
  `javascript:` URIs in `href`. A vendored htmx with no inline scripts fits `default-src 'self'`; htmx's
  `hx-on` inline handlers do not (htmx essay).
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Strict-Transport-Security` (only
  meaningful over HTTPS).
- Always quote attributes containing Jinja expressions (`value="{{ v }}"`).
- `TRUSTED_HOSTS` (new in 3.1) restricts the `Host` header.

## 7. Packaging: same image versus separate image

Repo facts:
- API image: `api/Dockerfile`, `python:3.13-slim`, runs gunicorn (2 workers x 4 threads, 60 s timeout) on
  port 8000, healthcheck on `/health`, non-root `appuser`. Built from context `./api` and published as
  `ghcr.io/ddobson/jellyhook-api` for amd64 and arm64 (`.github/workflows/docker-publish.yml`,
  `docs/guides/docker.md`).
- `COPY src ./src/` and dependencies installed from `uv export --frozen`; `uv.lock` lists
  `jellyhook-api` as `editable = "."`, so files under `api/src/api/` (including `templates/` and
  `static/`) should be present in the image. Read from `uv.lock`, not verified by building the image.
- `pyproject.toml` builds a hatchling wheel with `packages = ["src/api"]`. Whether non-Python files are
  included in a wheel was not checked (Hatch docs are outside the allowed sources). It matters only if
  the project is installed non-editable.
- API and worker must not import from each other (`CLAUDE.md`). Each has its own uv environment.
- `/hooks/*` has no auth unless `WEBHOOK_TOKEN` is set; the API warns at startup when unset
  (`docs/development/api-rules.md`).

Option A: serve the UI from the existing API image (new blueprint, for example under `/ui`)
- Python packages added: none for Flask-only (Jinja, session, flash, static serving, blueprints all
  present). Optional: a vendored `htmx.min.js` file (not a package).
- One container, one port (8000), one set of env vars; reuses `SECRET_KEY`, gunicorn and the healthcheck.
- Login-protected UI pages and webhook routes share a process, config object and port. Separation is by
  blueprint and `before_request` only. Cookie auth (UI) and bearer auth (webhooks) are independent.
- The image gains templates and static assets, and the UI is reachable on the same port Jellyfin
  already reaches.
- Gunicorn runs 2 workers: signed-cookie sessions are stateless so they work across workers; any
  in-memory state (for example login attempt counters) would be per worker.
- `MAX_CONTENT_LENGTH` of 1 MiB already applies to every route.

Option B: separate UI image
- Python packages added: none required beyond Flask, but it is a new package with its own
  `pyproject.toml`, `uv.lock`, Dockerfile, CI publish job and Compose service (duplicating what `api/`
  has). If it needs data held by the API or worker, it needs a way to reach it other than importing
  their code (the no-import rule); how it would read data is not answered here.
- Independent scaling, port, restart and network exposure (for example UI reachable only on a management
  network while the API port stays open to Jellyfin). Two images to publish and document.
- Needs its own `SECRET_KEY` and login secret.

What the UI would show or do (and so which of A or B is feasible) is not specified in JLYH-7 and was not
researched.

## 8. Reverse proxy and HTTPS

- Flask: behind a proxy, wrap `app.wsgi_app` in Werkzeug `ProxyFix` with the correct number of trusted
  proxies per header; "it can be a security issue if you get this configuration wrong". (Flask, Tell
  Flask it is Behind a Proxy)
- `SESSION_COOKIE_SECURE=True` only makes sense over HTTPS; the current Docker docs publish plain
  `http://<docker-host>:8000` and the image does not terminate TLS. (Flask, Config;
  `docs/guides/docker.md`)

## 9. Summary table (new Python packages)

| Concern | Built-in (no new package) | Needs adding |
| --- | --- | --- |
| Templating, blueprints, static | Flask + Jinja | nothing |
| Login session | signed-cookie `session` + `before_request` | nothing |
| CSRF token | hand-rolled with `secrets` + `session` | optional third-party package (not reviewed) |
| Form validation | manual checks on `request.form` | optional third-party package (not reviewed) |
| Interactivity | none (plain forms and links) | htmx file vendored into `static/` (no Python package; npm route adds Node) |
| Server-side sessions | not built in | extension (not reviewed) |
| Security headers | set manually in `after_request` | optional Flask-Talisman (named by Flask, not reviewed) |
| Login rate limiting | not built in | not covered by Flask docs |

## Not established

- Whether `api/src/api/errors.py` returns JSON only (would affect HTML error pages).
- Whether hatchling includes templates and static files in a non-editable wheel.
- Behaviour and licences of third-party packages (Flask-WTF, Flask-Talisman, Flask-Limiter, server-side
  session extensions): excluded by the primary-sources-only rule.
- Exact htmx file size (the docs page does not state it).
- Whether the image build actually installs the project editable from `/app/src`; inferred from
  `uv.lock`.

## Sources

- https://flask.palletsprojects.com/en/stable/templating/
- https://flask.palletsprojects.com/en/stable/blueprints/
- https://flask.palletsprojects.com/en/stable/quickstart/
- https://flask.palletsprojects.com/en/stable/config/
- https://flask.palletsprojects.com/en/stable/web-security/
- https://flask.palletsprojects.com/en/stable/patterns/flashing/
- https://flask.palletsprojects.com/en/stable/extensions/
- https://flask.palletsprojects.com/en/stable/deploying/
- https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/
- https://htmx.org/docs/
- https://htmx.org/essays/web-security-basics-with-htmx/
- Repo: `api/pyproject.toml`, `api/uv.lock`, `api/Dockerfile`, `api/src/api/{app,auth,config,routes}.py`,
  `docs/guides/docker.md`, `docs/development/api-rules.md`, `CLAUDE.md`
