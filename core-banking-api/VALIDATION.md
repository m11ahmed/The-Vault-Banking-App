# Validation — October 1, 2026

## Automated

The final command was:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

**135 tests passed in 327.650 seconds.** No tests were skipped and none failed.
The seven tests of the supplied starter's original gaps now pass as regressions.

Coverage includes exact money and validation, concurrent withdrawals, concurrent
same-key requests, persisted replay and conflict detection, transaction rollback,
account ownership, actual cookie login/logout/expiry/rotation, reviewer roles,
cross-origin request rejection, KYC version checks and audit rollback, real local
OCR, actual Chroma retrieval, and offline provider SDK integration.

The test run emits non-failing AnyIO resource warnings on simulated server-error
paths. These are recorded here; they did not produce a test failure.

## Browser

The local app was exercised through the actual in-app browser:

- Ayesha and Hassan sign-in/sign-out and separate owned account views.
- Deposit `20.10`, withdrawal `5.05`, transfer `10.05`, balances/history/filter.
- Assistant response reading the updated `12505.00` balance and cited KYC FAQ.
- PNG sample upload and real OCR matching the name and ID.
- KYC submission, reviewer rejection/reason, owner resubmission, reviewer
  approval, and customer status with both review decisions retained.
- Desktop 1280px and mobile 390px viewport overrides. Page width remained within
  the viewport; transaction tables have their own horizontal scroll container.
- No browser console errors were observed in the final inspection.

Saved browser images are in `artifacts/`.

## Live Gemini check — October 1, 2026

The saved private key was detected and `AI_MODE=gemini` enabled. The model was
updated to `gemini-3.5-flash-lite`; the former 2.5 configuration returned 404.
After restarting the localhost backend with outbound network access:

- Authenticated balance chat returned 200 in Gemini mode, used `get_balance`,
  and correctly reported the current synthetic balance of PKR `11525.10`.
- Account and transaction snapshots were unchanged by the assistant request.
- A real browser KYC question succeeded, used `search_banking_faq`, cited
  `[demo-kyc]`, and changed the dashboard badge to **Gemini connected**.
- Initial history and FAQ requests returned sanitized 503 errors. A diagnostic
  confirmed `APIConnectionError` caused by TLS `UNEXPECTED_EOF_WHILE_READING`.
  The key works, but those checks do not establish a consistently reliable
  network connection. The history question also failed on the browser retry;
  successful live transaction-history answers remain unverified.
- The updated offline assistant/Gemini suites passed: **33 tests in 71.605
  seconds**, with no failures. These are regression checks, not provider calls.

The key is private and excluded from the source package. The app cannot determine
the Google project's billing tier from the key.

## Pending external verification

- Consistent live Gemini connectivity and a successful history smoke check.
- OpenAI access and live model answers remain untested.
- PostgreSQL: locking implementation has not been tested on a running server.
- Docker: files are prepared; no Docker executable was available for a build.
- GitHub and hosted URL: no remote/account is connected; no public deployment
  has been made.

This is a synthetic portfolio system. The test results do not establish
production banking readiness, document authenticity or a Temenos integration.
