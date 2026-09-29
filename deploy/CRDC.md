# CRDC deployment

This deployment uses Linux/amd64 images produced by GitHub Actions. The CRDC host pulls
fixed GHCR digests and does not build source code. The only host port is the web
container's Authentik-protected 4183. Keep the data at /data/apps/article-crawler
and configuration at /home/sinopv/pv_reginald/article-crawler.

## First release

1. Verify that port 4183 is free, the target directories do not contain another
   application's data, and the external `authentik-internal` network exists.
2. Check out the reviewed Git commit into the configuration directory's
   `.release-source` folder directly from GitHub. Record the commit.
3. Copy `deploy/crdc.env.example` to a server-local `.env` (mode 0600).
   Generate unique PostgreSQL and RustFS secrets on CRDC. Fill in the image
   references using the successful GitHub Actions build's immutable digests.
   Keep `BIND_IP=127.0.0.1` until Authentik is configured and checked.
4. Create only the project data directories. RustFS needs its `rustfs` directory
   owned by uid/gid 10001; use a one-time project-scoped container command if
   host sudo is unavailable. Do not change ownership of /data/apps itself.
5. Run `docker compose --project-directory <config-dir> --env-file <config-dir>/.env
   -f <config-dir>/.release-source/compose.crdc.yaml config --quiet`.
   Pull the five fixed images, then start this Compose project only.
6. Verify database, Redis, RustFS, API, worker and web health. Check API search
   and a synthetic PDF reference workflow before exposing the host port.

## Authentik

In the existing CRDC Authentik Admin interface, create an application named
`Medical literature` with slug `article-crawler` and a Proxy provider in
**Forward auth (single application)** mode. Set External host to the exact
browser URL, including `:4183`; use the existing authentication and
authorization flows. Add the application to the existing Embedded Outpost.
Use an Authentik group/policy if access should be narrower than all signed-in
users. The production web image contains the matching Nginx forward-auth
configuration. It does not change the shared gateway.

After the provider is active, set `BIND_IP` in the server-local `.env` to the
CRDC private address and recreate only this project's web container. Verify
that an unauthenticated browser gets an Authentik redirect, login returns to
the app, and `/api/health` is not accessible without a session. `/healthz`
is deliberately unauthenticated for container health.

## Backup and rollback

The first deployment has no preceding project data. Before later updates,
record the running image digests and Git revision. Stop this project's web,
worker and API containers to prevent new writes, then create a PostgreSQL custom
dump. Stop this project's Redis and RustFS containers before archiving their
project directories under `/data/apps/article-crawler/backups`. Verify dump
readability with `pg_restore --list`, archive listings, and file sizes; restart
the stopped project services and confirm health. Keep the prior image references
and backup until the new release passes health and browser checks.

For a code-only rollback, restore the prior digest references in the
server-local `.env`, pull those images, and run `docker compose
--project-directory <config-dir> --env-file <config-dir>/.env
-f <config-dir>/.release-source/compose.crdc.yaml up -d` for this project.
Restore data only if a migration changed schema or data, using the matching
backup and code version. Test a restore of the project data before relying on
the backup. Never use `docker compose down -v`.
