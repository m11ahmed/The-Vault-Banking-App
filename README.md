# Vault — Core Banking + AI Portfolio

The application and its complete setup guide are in
[core-banking-api](core-banking-api/README.md).

FastAPI banking workflows, exact integer-paisa money, authenticated customer
accounts, persisted payment retry protection, a read-only banking assistant,
local OCR and audited human KYC review, with a responsive dashboard.

![Vault banking dashboard](core-banking-api/artifacts/banking-desktop.jpg)

```powershell
Set-Location core-banking-api
.\.venv\Scripts\python.exe -m app.seed_demo
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000/demo>. A fresh checkout needs Python 3.12 and the
dependency installation documented in the app README.

See [the demonstration guide](core-banking-api/DEMO_GUIDE.md) and
[validation results](core-banking-api/VALIDATION.md). The previous full suite
passed **135 tests**, and the latest assistant update passed **40 targeted tests**.
Desktop/mobile browser workflows were checked. Live Gemini balance, transaction
history and FAQ requests succeeded on October 2, 2026; provider connectivity has
been intermittent. Public app deployment remains pending. All accounts, balances
and identity documents are synthetic; this is a portfolio project, not a bank
or a Temenos integration.
