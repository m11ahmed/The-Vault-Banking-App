# Portfolio demo and publishing handoff

Start with `README.md` for setup. The current local app is at
<http://127.0.0.1:8000/demo>. Synthetic users share `VaultDemo!2026`:
`ayesha.customer@example.com`, `hassan.customer@example.com`, and
`reviewer@example.com`.

## A three-minute presentation

1. **Banking:** sign in as Ayesha, make a small deposit/withdrawal, and show the
   new balance and transaction history. Explain integer paisa and atomic writes.
2. **Transfers:** sign in as Hassan in a separate browser session, copy his
   account ID, and transfer synthetic funds from Ayesha. Show both accounts'
   own receipts. Explain why a repeated request cannot send the money twice.
3. **Assistant:** ask for the balance, last five transactions and KYC requirements.
   Show read-only tools and FAQ sources. State that demo mode is rule-based;
   real LLM conversation becomes available after provider configuration.
4. **KYC:** upload `samples/demo-id.png`, inspect the extracted name/ID and submit
   the fields. Sign in as the reviewer, record a decision with a reason, and
   show the owner the status and audit history. Explain that OCR supports a
   reviewer and does not authenticate identity documents.
5. **Engineering:** show the architecture in README and run the regression suite.

The local walkthrough used Ayesha's synthetic record to demonstrate rejection,
resubmission and approval; her current KYC status is verified. Use a fresh
customer for another complete submission demo. Seeding is idempotent and never
resets balances, passwords or reviews.

Screenshots are in `artifacts/`. `artifacts/demo-walkthrough.gif` is an animated
sequence of screenshots, not a screen recording. It can accompany a README or
portfolio post. A spoken presentation can follow the steps above.

## GitHub description

> Mini core banking portfolio with FastAPI, authenticated customer/reviewer
> workflows, atomic integer-paisa transactions, persisted idempotency,
> a read-only LangChain/Chroma assistant, and local OCR-assisted KYC review.

Suggested repository name: `vault-core-banking-ai`.

This workspace's Git repository contains the `core-banking-api` directory.
The release ZIP contains that directory with its own complete README. For a
standalone repository, upload or extract the project contents at the repo root.
Never include `.env`, the virtual environment, banking databases or backups.

Publishing is still pending: no GitHub remote or hosting account is connected
to this workspace. A local commit/package is separate from a public URL.

## Hosting configuration

The included Dockerfile and Compose configuration are prepared; Docker was not
available here, so no container build or hosted deployment has been verified.

For a container host, persist `/data` across deploys. The image uses
`DATABASE_URL=sqlite:////data/banking.db` and `CHROMA_DIR=/data/chroma`.
Use HTTPS with `COOKIE_SECURE=true`. Keep `AI_MODE=demo` until the provider key
is set in the hosting service's secret settings. Run only synthetic data.

For a publicly accessible portfolio demo with sample profiles, deliberately
enable `DEMO_MODE=true` and run `python -m app.seed_demo` once. Those demo
credentials are public and shared. For a deployment with private profiles,
keep `DEMO_MODE=false`, do not seed public credentials, and provision reviewer
users through an administrative process. Disabling the demo hint does not
delete already seeded users.

SQLite is the verified backend. PostgreSQL locking code exists but needs its
driver and real database verification before choosing that deployment path.
Do not put SQLite on an ephemeral filesystem and expect data to survive a
restart. `/health` provides the application health endpoint.
