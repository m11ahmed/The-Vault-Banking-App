# Validation — October 2, 2026

## Automated

The last full-suite run before the Gemini connectivity update used:

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
  this is recorded as the original failure before the connectivity update below.
- The updated offline assistant/Gemini suites passed: **33 tests in 71.605
  seconds**, with no failures. These are regression checks, not provider calls.

The key is private and excluded from the source package. The app cannot determine
the Google project's billing tier from the key.

## Gemini connectivity update — October 1, 2026

The user reported that successful prompts were followed by repeated failures.
A new diagnostic reproduced `APIConnectionError` with `httpx.ConnectError` and
TLS `UNEXPECTED_EOF_WHILE_READING`, before any Google HTTP response. No quota
rejection was observed in that diagnostic.

The service now retries one connection-setup failure per chat after 0.5 seconds,
reusing the identical model round without executing a read tool again. SDK
retries remain disabled; HTTP errors, read/write failures and timeouts are not
retried. Connection failures return a clear 503 message, timeouts return 504,
and the dashboard updates its previous connection badge after a failed request.

- The updated targeted suites passed: **40 tests in 85.458 seconds**. Seven new
  regressions verify retry bounds, exact replay, preserved tool output/signatures,
  no duplicate tool execution, sanitized errors and excluded retry categories.
  The full suite has not been rerun after this isolated assistant update.
- A live authenticated history chat returned **200**, used
  `get_recent_transactions`, and correctly listed the last five synthetic
  transactions. The server log confirmed that the new connection retry recovered
  an initial failure. This verifies a recovered request, not continuous network
  availability.
- A subsequent browser request still failed after the connection retry. The
  updated UI showed **Gemini unavailable** and the specific connection message,
  confirming that it no longer leaves a stale connected badge on that failure.
  The external connection problem remains intermittent.

## Live Gemini recheck — October 2, 2026

The local server was healthy and the saved source tree unchanged before this
recheck. The user confirmed they were still using the same internet connection.
Three sequential authenticated chat checks all returned **200** in Gemini mode:

- Balance used `get_balance` and correctly returned PKR `11525.10`.
- History used `get_recent_transactions` and returned the current last five
  synthetic transactions with the correct amounts and balances.
- The KYC question used `search_banking_faq` and returned the `demo-kyc` source.
  This request also included the preceding successful history conversation.

Account and transaction snapshots remained unchanged. The earlier connection
failure did not recur during this sequence. This successful recheck does not
establish continuous provider availability or verify the Google billing tier.
No new application code was needed; the existing connection retry remains.

## Pending external verification

- Consistent live Gemini connectivity remains dependent on the external network.
- OpenAI access and live model answers remain untested.
- PostgreSQL: locking implementation has not been tested on a running server.
- Docker: files are prepared; no Docker executable was available for a build.
- Public app hosting: no running public deployment has been made. Source is
  available in [The Vault Banking App](https://github.com/m11ahmed/The-Vault-Banking-App).

This is a synthetic portfolio system. The test results do not establish
production banking readiness, document authenticity or a Temenos integration.
