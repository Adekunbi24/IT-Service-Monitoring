# IT Service Monitoring Platform

A portfolio project built with **Python, FastAPI, PostgreSQL, Docker Compose, JavaScript, and Chart.js**. The application monitors administrator-registered HTTPS websites and displays status, response times, historical uptime, and alert events.

> **Status:** Local functionality has been tested by the project owner. This repository includes **unverified AWS infrastructure templates**. No AWS deployment is claimed.

## Features

- Administrator password login using Argon2 password verification and signed session cookies; CSRF token required for browser-originated changes.
- Login rate limiting (five attempts per minute per client IP in the current single-process setup).
- Administrator-only website registration and activation/deactivation.
- Background HTTP checks with per-site interval and slow-response threshold.
- PostgreSQL monitoring history and DOWN/RECOVERY/SLOW/NORMAL alert events.
- Public read-only dashboard with charts and tables.
- Basic error handling, structured log format, DNS public-address checks at registration and before checks, and `/health` endpoint.

## Architecture

```text
Browser -> FastAPI /dashboard + JSON API
                |-> admin login, CSRF, website management
                |-> background HTTP monitor -> HTTPS websites
                |-> PostgreSQL (services, checks, alerts)
```

**Production template:** Browser -> HTTPS (Caddy) -> FastAPI -> PostgreSQL, all on one EC2 instance. Terraform provisions EC2, security group, and Session Manager IAM permissions. The app deployment itself is manual and intentionally deferred until AWS access and a domain are available.

## Local setup

Requirements: Docker Engine / Docker Desktop with Compose. Copy `.env.example` to `.env` and replace every placeholder with strong unique values. `ADMIN_PASSWORD_HASH` must be a real Argon2id password hash (not a plaintext password). Keep `$` signs intact; see the example's single quotes.

```bash
cp .env.example .env
# Edit .env locally, never commit it
# Start only after all secrets are configured
docker compose up -d --build
```

Open `http://127.0.0.1:8001/dashboard`. Check container logs with `docker compose logs --tail=100 app`. Stop with `docker compose down` (do **not** use `-v` unless you intend to delete the database volume).

**Existing database:** `init.sql` runs automatically only on the first initialization of an empty PostgreSQL data directory. Back up the `postgres_data` volume before any migration or destructive operation.

### API overview

| Method | Endpoint | Access |
| --- | --- | --- |
| GET | `/dashboard` | Public |
| GET | `/health` | Public |
| GET | `/check`, `/uptime`, `/alerts`, `/uptime-history`, `/response-times`, `/response-time-history` | Public |
| POST | `/admin/login` | Password, rate limited |
| GET | `/admin/csrf-token` | Admin session |
| POST | `/admin/logout` | Admin session + CSRF |
| GET | `/services` | Admin |
| POST | `/services` | Admin + CSRF for browser session |
| PATCH | `/services/status` | Admin + CSRF for browser session |

## HTTP status interpretation

2xx and 3xx responses are classified as `UP` (server responded). 4xx and 5xx are classified as `DOWN`, and connection/DNS errors as `ERROR`. **3xx does not verify the final destination**, because automatic redirects are disabled. Historical statistics are not retroactively recalculated when classification changes.

## Tests and checks

```bash
python3 -m py_compile main.py
python3 -m pytest -q tests
# If Node.js is installed, check dashboard script syntax manually or in CI.
```

These tests cover static structure and syntax, **not** an end-to-end database or browser test. Verify add/deactivate/login again locally after updating files.

## AWS / Terraform (deferred)

**Cost warning:** EC2, public IPv4, EBS, data transfer, and any DNS/domain charges can apply even if AWS credits are expected. Credits are not guaranteed; review billing and budgets first. Terraform itself does not make infrastructure free.

**Prerequisites:** AWS account permissions, AWS CLI configured, Terraform >= 1.6, default VPC with default subnets, an owned domain pointing to the instance, and a publicly accessible source repository (or a separate private-code deployment workflow). The Terraform template targets Amazon Linux 2023 x86_64 and uses AWS Systems Manager Session Manager rather than opening SSH.

```bash
cd terraform
terraform init
terraform fmt -check
terraform validate
terraform plan
# Review the plan and costs before manually approving:
# terraform apply
```

Terraform creates **only infrastructure**, not an automatically running website. Once EC2 is available:

1. Configure a DNS A record for your domain to the instance public IP. Public IP may change on replacement/restart; use a stable addressing plan before long-term operation.
2. Connect with AWS Systems Manager Session Manager. The instance requires outbound access to SSM endpoints; confirm it is registered as a managed node.
3. Install Docker Compose plugin if it is not installed, clone your reviewed repository into `/opt/it-service-monitoring`, and create a secure `.env` file there (permissions 600). Do not store secrets in Terraform variables, state, or Git.
4. Set `DOMAIN=monitor.example.com` in `.env` (replace with your actual DNS name), and set `COOKIE_SECURE=true`.
5. Run `docker compose -f compose.prod.yaml up -d --build` from the project directory. Check `docker compose -f compose.prod.yaml ps` and logs.
6. Confirm HTTPS certificate issuance, dashboard, admin login, DNS filtering, and data persistence before sharing publicly.

**Caution:** The Terraform EC2 root EBS volume is encrypted but not separately protected from `terraform destroy`; make a backup/snapshot before deleting infrastructure. This is a portfolio single-instance architecture, not high availability.

To remove infrastructure after backups: `terraform destroy`. Review deletion and data-loss impact first.

## Security limitations / follow-up before public launch

- **SSRF/DNS rebinding:** DNS is rechecked before monitoring, but HTTPX independently resolves names when connecting. This is **not** a complete protection against DNS rebinding. Do not expose arbitrary URL registration to untrusted administrators. For production, add network egress filtering and DNS pinning / a safe outbound proxy before public launch.
- **Session revocation:** Starlette sessions are signed client-side cookies; logout clears the browser cookie but cannot revoke an already stolen valid cookie. Use server-side session storage if required.
- **Rate limiting:** Current SlowAPI in-memory limits are per process; distributed deployments need shared storage and correct trusted-proxy configuration.
- **Production HTTPS:** The separate `compose.prod.yaml` runs Caddy and sets secure cookies. Do not set secure cookies on the local HTTP-only setup.
- **Secrets:** Never commit `.env`, password hashes, cloud credentials, Terraform state, or private keys. Rotate secrets if exposed.
- **Operational:** Consider automated backups, monitoring of the monitoring service, and dependency updates before relying on it for critical production workloads.

## Resume summary (accurate once local re-testing passes)

> Developed a website monitoring platform using FastAPI, PostgreSQL, Docker, and Chart.js, featuring administrator-managed service checks, uptime analytics, response-time trends, alert history, and session-based access controls. Prepared Terraform configuration for an AWS EC2 deployment.

**Do not describe the AWS deployment as completed until it has actually been deployed and verified.**
