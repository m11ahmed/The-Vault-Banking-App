# Historical review of the supplied Core Banking API

> The September 9 findings below describe the original ZIP, not the current app.
> The October 1 implementation resolves the original financial/access-control
> gaps in the local SQLite application and adds authenticated banking, assistant,
> OCR and KYC review workflows. The current behavior and commands are in README.md.

## Current resolution — October 1, 2026

| Original finding | Current implementation | Regression evidence |
| --- | --- | --- |
| Floating-point money and sub-paisa amounts | Integer paisa storage, bounded two-decimal validation and monetary strings in API responses | `test_known_gaps.py`, `test_banking_controls.py` |
| Concurrent withdrawals spend the same funds | Database-coordinated money writes: SQLite writer reservation and ordered account locking for the PostgreSQL code path | `test_banking_controls.py`; SQLite tested, PostgreSQL not run |
| Account and opening history commit separately | Account and opening transaction commit together and roll back together | `test_known_gaps.py`, `test_workflows.py` |
| Transfer retries move money twice | Mandatory persisted `Idempotency-Key` for deposit/withdrawal/transfer; identical retry replays its receipt and conflicting data returns 409 | `test_known_gaps.py`, `test_banking_controls.py` |
| Accounts and KYC approval have no access controls | Hashed passwords, revocable cookie sessions, customer ownership and reviewer-only decisions; sender-only transfer receipt | `test_auth.py`, `test_kyc_review.py`, `test_banking_controls.py` |
| KYC decisions can overwrite reviewed states without an audit | Pending-only reviewer updates require a reason and the opened submission's `expected_submitted_at`; stale cards are rejected and decisions have an atomic audit; rejected-only resubmission preserves history | `test_kyc_review.py` |
| Startup cannot migrate existing SQLite columns | Versioned v1-to-v2 conversion with backup and preserved records; legacy accounts stay unowned and cannot be claimed by matching email | `test_migrations.py` |

The original gap tests are now regression checks rather than intentional
failures. Run all suites with the README command; these tests use temporary
databases and synthetic identities. Exact current suite totals belong with the
final verification result, not the historical baseline count below.

The assistant now enforces customer account ownership before invoking read-only
tools. The dashboard includes registration/login, money forms, paginated history,
chat, OCR-assisted editable KYC submission, review status/audit, and reviewer
decisions. Local OCR analysis never approves a record or retains uploaded images.

The project remains a synthetic single-currency portfolio app, with no Temenos
connection, payment network, full double-entry ledger or document-authenticity
verification. Live Gemini/OpenAI requests, PostgreSQL correctness, Docker execution
and a public deployment are not verified. Container files are prepared, but
Docker was unavailable on this machine.

## Original September 9 baseline

Reviewed locally on Windows on September 9, 2026, using Python 3.12.14 and the
supplied pinned dependencies. Source: `core-banking-api.zip` supplied by the user.

### Baseline result

The starter ran, and its advertised account, deposit, withdrawal, transfer,
transaction-history, and KYC status routes worked in the tested ordinary flows.
It was a useful local prototype. Its banking correctness and access controls
required further work before adding authenticated customer-facing AI tools or
deploying with real customer information.

**At that baseline, 31 automated tests ran: 24 passed, 7 failed.** The failing
suite asserted desired safeguards that the supplied implementation did not
provide. They were ordinary failures, not skipped tests or expected-failure
markers; these results do not describe the current October 1 implementation.

### Baseline verified workflows

- Root response, Swagger HTML, and OpenAPI schema respond successfully.
- Account creation, listing, lookup, opening deposit history, and ordinary
  duplicate-email rejection work.
- Deposit and withdrawal update balance and transaction history; withdrawing
  the exact available balance succeeds.
- Insufficient-funds requests leave balances and transaction history unchanged.
- Transfers update both accounts and record matching outgoing/incoming amounts.
- Self-transfers, missing accounts, nonpositive amounts, and inactive accounts
  are rejected in the tested cases.
- History is returned newest first in the tested sequence.
- KYC submission, retrieval, duplicate rejection, approval, and rejection work.
- A simulated failure writing a transfer's incoming transaction rolls back both
  balance updates and both transaction records.

### Baseline reproduced defects and missing safeguards

The following reproductions and proposed next changes were recorded against the
starter. Their local resolutions are documented in the October 1 table above.

#### 1. Concurrent withdrawals can overspend an account

Location: `app/routers/transactions.py`, `withdraw`, and the same read-modify-write
pattern in `deposit` and `transfer`.

With a starting balance of 100, two overlapping withdrawals of 80 both return
HTTP 200. Each request reads 100 before either writes. Both then write a balance
of 20 and record a withdrawal. The stored balance and transaction history no
longer reconcile. The test coordinates the reads while leaving the route's SQL,
validation, and commits intact. Other concurrent operations were not exhaustively
tested.

Next change: coordinate writes at the database level with conditional updates
or appropriate transaction locking, and test the chosen database's behavior.
Using a single commit for each transfer does not by itself solve this race.

#### 2. Opening account creation and its deposit history are separate commits

Location: `app/routers/accounts.py`, `create_account`.

The account is committed before its opening transaction. A simulated failure
inserting that transaction returns HTTP 500 but leaves the funded account in
the database. This differs from the transfer rollback check, which passes.

Next change: flush the account to obtain its ID, create the opening transaction,
and commit both together.

#### 3. Money uses floating-point values

Location: `app/models.py`, the `Float` columns; `app/schemas.py`, monetary `float`
fields; arithmetic in the transaction routes.

Opening with 0.1 and depositing 0.2 produces a balance of
`0.30000000000000004`. A deposit of `0.001` is also accepted. The two-decimal
test is a proposed PKR demo requirement; the original API has no currency or
minor-unit policy at all.

Next change: choose one demo currency and precision, validate inputs, and store
integer minor units or use a verified exact-decimal database representation.
Apply this consistently to amounts, balances, history, and schema migration.

#### 4. No authentication or reviewer authorization

Location: all route modules, including account listing and `verify_kyc`.

Without any credentials, the tests can list accounts and approve KYC. Inspection
also shows no authentication dependency on balance, money-movement, or KYC-read
routes. A KYC reviewer endpoint exists by name, but it has no reviewer role
enforcement.

Next change: define customer/reviewer identities, authenticate requests, and
enforce ownership and roles in the backend before connecting customer-specific
assistant tools.

#### 5. Repeating a transfer repeats its financial effect

Location: `app/routers/transactions.py`, `transfer`.

Sending the same transfer of 10 twice, including the same `Idempotency-Key`
header, moves 20. The original API does not promise or implement that header;
this test documents an important missing retry safeguard.

Next change: store a request key and request fingerprint with the transfer in
the same transaction. Replay the saved result for an identical retry and
reject reuse with different request data.

### Additional baseline observations from code inspection

- There is no included test suite in the original ZIP. The tests in this
  workspace were added during this review; the earlier claim of end-to-end
  testing cannot be independently confirmed from the archive alone.
- KYC is manual text entry and a status update. There is no document upload,
  OCR, matching, document-authenticity check, reviewer audit record, or rejection
  reason. The endpoint can also change a previously reviewed status.
- Transfer history contains two entries, but there is no complete double-entry
  ledger with balanced postings for every money movement.
- The PostgreSQL "change one line" instructions are incomplete: the SQLite-only
  `check_same_thread` argument is always passed. A driver, connection options,
  migrations, and database-specific correctness tests are also needed.
- Startup creates tables through `create_all`; it does not migrate existing
  columns when the models change.
- The supplied dependency versions were preserved. Package compatibility was
  checked locally; this was not a dependency vulnerability audit.

### Files added or updated during the baseline review

- Added `tests/`, with isolated temporary databases and synthetic records.
- Added `requirements-dev.txt` for the HTTP test client.
- Added `.gitignore` to exclude local databases, secrets, Python caches, and
  virtual environments.
- Updated `README.md` with Windows commands, measured test results, and corrected
  setup limitations.
- Added this report. The original `app/` files and `requirements.txt` were not
  modified, and no GitHub repository was created or pushed.

### Historical recommended implementation order

1. Exact money representation, one-transaction account creation, coordinated
   balance updates, and idempotent money movements.
2. Authentication, account ownership checks, reviewer permissions, and audit
   records.
3. PostgreSQL configuration and migrations, with the financial tests run against
   PostgreSQL as well as the local development setup.
4. Authenticated assistant balance/history tools and document retrieval.

The ordering above reflects the original review recommendation. Exact money,
atomic writes, retries, authentication, reviewer audit, authenticated assistant
tools and local document extraction are now implemented. SQLite migration and
database configuration are included; PostgreSQL-specific validation remains a
future task. The README describes the current all-tests command. The original
test count is historical evidence, not a current intentional-failure claim or
a banking-system certification.
