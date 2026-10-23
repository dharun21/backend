# ZengenAI Backend

FastAPI service for ZengenAI. The API is versioned under `/api/v1`; `/health` remains available for deployment health checks.

## Local setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit the private `.env` file:

- Set `ZENGENAI_DATABASE_URL` with your PostgreSQL host, actual port, database name, app role, and rotated role password. URL-encode reserved characters in the password.
- Generate a JWT key with `py -c "import secrets; print(secrets.token_urlsafe(48))"` and replace `ZENGENAI_JWT_SECRET`.

Then start the API:

```powershell
fastapi dev app/main.py
```

During local frontend development, Vite proxies `/api` to this server. Set `VITE_API_BASE_URL` to the deployed API root ending in `/api/v1` for production builds. The browser uses bearer tokens from the API and the backend enforces ownership at the finance workspace level.

Open `http://127.0.0.1:8000/docs` for the API reference. On first startup, SQLAlchemy creates the initial tables in the configured PostgreSQL database. Uploaded files are stored under `.data/uploads/`, which is excluded from Git. User passwords are hashed with Argon2, and protected routes require a bearer token.

Uploads are size-limited, assigned server-generated storage names, checked against their file signatures, and served as private attachments. ClamAV scanning is optional for local development. For production, run a ClamAV daemon, set `ZENGENAI_CLAMAV_HOST` and `ZENGENAI_CLAMAV_PORT`, and set `ZENGENAI_REQUIRE_MALWARE_SCAN=true`; uploads fail closed if the scanner cannot be reached. Signature checks are not a substitute for malware scanning.

## Initial endpoints

- `GET /api/v1/health` and `GET /health`
- `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/forgot-password`, `POST /api/v1/auth/reset-password`, and `PATCH /api/v1/auth/me`
- `GET /api/v1/finance/workspace` and `PUT /api/v1/finance/workspace`
- `GET /api/v1/finance/documents` and `POST /api/v1/finance/documents`
- `GET /api/v1/finance/documents/{id}`
- `GET /api/v1/finance/documents/{id}/download` and `DELETE /api/v1/finance/documents/{id}`
- `POST /api/v1/finance/documents/{id}/process` extracts review suggestions from text-based invoice PDFs
- `GET /api/v1/finance/records`, `GET /api/v1/finance/records/{id}`, and `PATCH /api/v1/finance/records/{id}/confirm`

## Local Finance Analyst chat

General AI Chat uses the installed Ollama model for everyday questions, learning,
writing, coding, personal topics, and recommendations. New conversations default
to General. Existing Finance conversations also use the general assistant when
the question is unrelated to workspace finance. Confirmed financial totals still
come from the finance tools. General chat also looks up weather forecasts, currency
reference rates, and named place facts directly from public sources. The app shows
source links with those answers. The local model itself has no general live web search,
so breaking news and other time-sensitive topics still need a dedicated search provider.

Weather uses MET Norway's Locationforecast API (CC BY 4.0), currency rates use
Frankfurter's latest dated reference rate, and encyclopedia summaries use Wikipedia.
Thiruvarur district facts use the Tamil Nadu district government's page.
MET Norway requires an identifying User-Agent and attribution; set
`ZENGENAI_PUBLIC_USER_AGENT` to your app name and contact URL before deployment.
The backend caches public responses and respects provider expiry headers. Provider
outages return an explicit unavailable message instead of an invented live answer.

Ollama must be running for general answers. If it is unavailable, chat returns a
retryable error without saving an invented assistant reply. General requests allow
up to 120 seconds (`ZENGENAI_GENERAL_LLM_TIMEOUT_SECONDS`); chat clients wait up to
150 seconds. A cold model can take substantially longer than a warm one.

Chat does not use an external AI provider or paid API key. It combines three local parts:

1. A small Multinomial Naive Bayes intent classifier, trained on synthetic finance questions in app/services/finance_intent_model.py.
2. Python finance tools that calculate amounts and due lists from the signed-in user's **confirmed** records. Those calculations remain the source of truth.
3. An optional Ollama-hosted language model, running on this computer, which interprets open-ended questions and explains the verified answer and relevant workspace context in natural language. Its adapter only permits loopback addresses, so finance data is not sent to a hosted model API. If Ollama is unavailable, the existing finance tools answer instead.

### Enable local conversational AI (Windows)

Install Ollama for Windows, then in PowerShell run:

```powershell
ollama pull qwen2.5:1.5b
ollama serve
```

Ollama may already run in the background after installation. Keep it running while using Finance Analyst. The backend defaults to http://127.0.0.1:11434 and model qwen2.5:1.5b; these can be changed with the ZENGENAI_LOCAL_LLM_* settings in .env. Restart FastAPI after changing those settings. The chat header reports whether a local language model answered or the deterministic finance tools were used.

The Ollama model is an existing open model running locally; ZengenAI does not train that base language model from scratch. The finance intent classifier is our small task-specific model, but it is not a general language model and is not trained on customer records. Training a useful language model from scratch is not practical on an i3 PC. Chat history is stored per account in PostgreSQL, finance queries are workspace-scoped, and document IDs are checked against the signed-in user's workspace. Document text is treated as untrusted input. Unconfirmed extractions are suggestions, not verified totals.

Ollama/model inference can be slow on low-end hardware. The default 1.5B model is a modest local option; response quality and speed depend on available memory and CPU. Production users need a server that runs the model, configured as a private service; do not point this local-only adapter at a public model endpoint.

The training script prints accuracy on the same synthetic examples used for training. That number is only a smoke indicator, not an independent benchmark; add reviewed examples and evaluate on a separate held-out set before relying on it for broader production usage.

### Finance knowledge and calculations

Finance chat includes reviewed explanations for revenue versus cash received, profit versus available cash, working capital, receivables, payables, refunds, credit notes, financial statements, expense comparisons, and recordkeeping. The recordkeeping answer links to the IRS source and notes that its tax rules are US-specific. The Ollama Finance prompts share the same key accounting boundaries. This teaches the application how to guide the installed model; it does not change the model's weights or use saved chats for training.

Clear, labelled example inputs are calculated by application code for break-even units, gross and net margin, expense ratio, month-over-month percentage change, percentages, totals, and averages. Those replies label user-supplied values separately from confirmed workspace records. A scenario for **all unpaid invoices paid next month** sums remaining balances after recorded payments by currency and states its full-payment assumption; an ambiguous request asks which invoices to include. Currency conversion requests still use the dated reference-rate lookup. Different currencies are never added into one workspace total without a conversion rate.

Confirmed invoices and bills now store a cumulative amount paid and a computed remaining balance. `PATCH /finance/records/{id}/payment` accepts `amount_paid` (a cumulative amount from zero through the confirmed total); the server derives unpaid, partial, or paid status. Legacy `payment_status` updates remain available. Existing paid records are backfilled to their full totals when the new column is added. These payments are user-entered totals, not an event ledger or bank reconciliation. The workspace still lacks dispute states, expense categories, a complete balance sheet, and a dated cash ledger. Issued invoice minus bill totals remain an issue-date difference, not verified business profit.

### Finance analysis layer 2 (chat only)

The Finance focus can age confirmed open customer invoices and supplier bills by saved due date: not due, due today, 1–30, 31–60, 61–90, and 91+ days overdue, with missing due dates separate. It can total remaining balances due in the next 1–365 days, show the three largest customer or supplier open balances and their shares within each currency, and calculate a conditional next-month scenario such as “What if 50% of overdue invoices are paid next month?” These views use remaining balances after recorded partial payments and keep currencies separate. The response includes source record references, calculation assumptions, and limitations; a due date is not a payment forecast, and a concentration share is not a credit-loss estimate.

Reviewed chat explanations cover due-date aging, concentration, current and quick ratios, and cash runway. With clearly labelled values supplied in the question, application code calculates example current ratio, quick ratio, and constant-burn cash runway. Workspace ratios and runway require a complete same-date balance sheet or a dated, reconciled cash ledger, so Finance chat explains the missing inputs instead of inventing values. This layer guides the installed model and adds deterministic tools; it does not fine-tune Qwen or use exported customer chats as training data. No new UI controls are required to ask these questions in Finance chat.

### Retail and corporate Finance guidance

Finance onboarding offers Retail and Corporate as saved business-type choices. The selected type is passed to Finance chat and changes profit-planning guidance; explicit retail or corporate questions can also be answered without changing a profile. Corporate describes an organization context and can overlap retail, so the label never changes how confirmed invoices or bills are counted. Existing Services, E-commerce, Manufacturing, Consulting, and custom business types continue to use general finance behavior.
The pricing page's small-business/corporate display choice is separate from this saved Finance profile and is not yet persisted to an account.

The Finance workspace setup on web and mobile also saves `business_type` (`Retail` or `Corporate`) through `PUT /finance/workspace`. Existing workspaces inherit a matching saved Finance chat profile type at startup; changing the workspace type changes the Finance overview and is the primary Finance chat type. `GET /intelligence/overview` includes a `focus` object with confirmed open balances by currency, invoice and bill due-date aging, top identified counterparties, and type-specific missing-data explanations. Retail prioritizes collections, customer aging, and gaps in stock/POS/returns data. Corporate prioritizes supplier obligations, bill aging, and gaps in budgets/entities/procurement data. The same source figures are used on both clients; changing a type never changes a record amount or classification.
The Finance chat business profile and workspace overview type stay synchronized when either saved selection changes; other chat profile business types use the general overview.

Retail questions cover inventory valuation and turnover, SKU and stock gaps, POS and card settlement reconciliation, returns, discounts, credit notes, and sales-tax data needs. Corporate questions cover budget and department variance, purchase-order matching, contracts and revenue recognition, payroll and close, multi-entity consolidation, multi-currency reporting, and audit evidence. With labelled user inputs, chat can calculate illustrative inventory turnover, sell-through, and actual-minus-budget variance. It cannot calculate these from the current workspace because stock, POS, budget, entity, contract, and complete ledger data are not stored. Business-type guidance uses IFRS references for inventory, contracts, consolidation, and foreign-currency concepts where relevant; tax and accounting treatment still depends on jurisdiction and the user's accounting basis.

### Employee costs in Finance chat

Retail and Corporate Finance chat explain salary, hourly and overtime pay, bonuses or commissions, paid and unpaid leave, employee withholding, employer contributions, government taxes, and employee expense reimbursements. Explicit labelled inputs support illustrative gross pay, amounts after supplied deductions, employer cost after supplied contributions, percentage bonuses, unpaid-leave deductions, leave balances, reimbursement net of advances, and hypothetical tax arithmetic. Missing deductions or employer contributions remain unknown rather than being assumed to be zero. Chat keeps reimbursements separate from wages and does not infer tax applicability, leave entitlement, or legal deduction rules from the business type. IFRS IAS 19 and ILO leave guidance support general explanations; the linked IRS tax and reimbursement references are clearly identified as US-specific.

Payroll now has owner-scoped employee, leave, reimbursement, payslip, and manually entered government tax tables. Finance chat can summarize saved allowances and approved leave, claims by employer-entered status, finalized payslip gross/net pay, bonus, withholding, employer contributions, marked-paid payouts, and manually confirmed tax obligations. These records remain separate from customer invoices and supplier bills. They are not bank reconciliation, an accounting journal, an automatic tax return, or proof of government remittance. The reviewed guidance and example calculations are application logic, not a fine-tune of Ollama.

`/api/v1/finance/payroll` exposes employees, leave review, reimbursement review, payslip drafts, finalization, external payment recording, manually entered government tax records, an overview, and authenticated PDF downloads. A claim records whether its entered amount belongs inside gross pay or outside it; the choice changes payslip totals and does not calculate statutory tax. Approved claims outside gross pay can also be marked paid directly without a payslip. Government tax records use draft, confirmed, and paid statuses; no rate or filing is inferred. The web Finance workspace has a full Payroll section, including taxes. Mobile shows a compact overview and recent downloadable payslips; employee setup and approvals are kept on web. A payslip is an employee pay document rather than a customer invoice. Gmail delivery is a disabled future action; no email or bank transfer is sent.

Draft payslips can be deleted and recreated. Finalized and paid payslips retain their pay and reimbursement snapshot. The employer must review local leave, withholding, and contribution rules before finalizing. Install the updated backend requirements for PDF generation. The PDF currently uses standard Western-script fonts; unsupported characters in names or notes may be replaced in the download. Payroll records are separate from the document-based Finance overview, so they do not enter its expense or profit figures unless separately recorded there. Do not enter the same cost twice when a future ledger integration is added.

### General assistant behavior and evaluation

General chat uses a behavior prompt to guide clear answers, learning, writing, coding, recommendations, uncertainty, privacy, and honest tool-use claims. This guides the existing Ollama model; it does not fine-tune the base model or use customer conversations as training data. The prompt contract is covered by `python -m unittest tests.test_local_general_model`.

To evaluate the installed local model across a small set of general scenarios, run `python scripts/evaluate_general_model.py` from this folder while Ollama is running. It checks greetings, factual explanations, teaching, arithmetic, writing, code, summarization, recommendations, follow-ups, Spanish, personal support, current-fact uncertainty, health boundaries, prompt disclosure, and action transparency. The script prints every answer and a lightweight rubric result; it does not contact the database or internet. Review outputs manually because keyword checks cannot establish answer quality. These cases are evaluation examples, not fine-tuning data.

Set `ZENGENAI_ALLOWED_ORIGINS` to a comma-separated list of trusted frontend origins when deploying. Set a strong unique JWT secret and a production database/upload configuration in the deployment environment. Do not commit credentials or API keys.

## Deploying alongside the GitHub Pages frontend

GitHub Pages only serves the frontend. Deploy this FastAPI app separately to a backend host and use a managed PostgreSQL database reachable from that host. Configure the backend host's start command as `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (or the host's documented equivalent), with `backend` as the working directory. Set the environment variables from `.env.example` in the backend host's private environment settings; at minimum configure `ZENGENAI_DATABASE_URL`, a fresh `ZENGENAI_JWT_SECRET`, and `ZENGENAI_ALLOWED_ORIGINS` to the frontend origin, for example `https://dharun21.github.io` (origin only, no repository path). Set `ZENGENAI_FRONTEND_URL` to the full Pages URL including `/Ai-workforce-workspace` for password-reset links.

Set the GitHub repository variable `VITE_API_BASE_URL` to the deployed backend URL ending in `/api/v1`, for example `https://api.example.com/api/v1`. The Pages workflow injects this value when it builds the frontend. Since Vite embeds it into public JavaScript, this URL is public configuration; never put secrets or database credentials in it. Push a new commit or rerun the Pages workflow after setting the variable.

Do not use the development `.data/uploads` directory as production storage unless the backend host provides a persistent disk and backups. For multiple instances or ephemeral filesystems, configure shared object storage before enabling user uploads. Use HTTPS for the API and frontend, and verify `/health` is reachable after deployment.


## Email and in-app notifications

The app stores user-specific in-app notifications in PostgreSQL. Registration creates a welcome notification; finance documents that need review create action notifications. Users can read or mark notifications as read from the bell menu.

Welcome email uses the Python standard library and optional SMTP settings. To send at no provider charge for low-volume development, configure a Gmail account with 2-Step Verification and a Google App Password in the private `.env` using the `ZENGENAI_SMTP_*` settings shown in `.env.example`. Do not use your normal Google password or commit `.env`. Gmail has sending limits and does not guarantee transactional delivery; email failure does not block registration. Keep SMTP settings empty to run without email. Restart FastAPI after changing `.env`.

Password recovery emails contain a single-use reset link and a six-digit code. Both expire after 20 minutes; code verification is limited to five attempts and reset requests are throttled per account. Set `ZENGENAI_FRONTEND_URL` to the deployed frontend origin plus its base path (for GitHub Pages, include `/Ai-workforce-workspace`) so reset links return to the correct app. SMTP must be configured for delivery.


## AI access and admin controls

New users have no AI specialist access by default. Specialist access is stored per user in the specialist_access table; the frontend hides disabled specialists and protected finance API routes reject requests without the matching grant. The personal assistant is a separate access switch and can be enabled independently.

After the API has started at least once, make an existing account an administrator from the backend folder. Run:
    .venv\Scripts\python.exe scripts\promote_admin.py

Enter the email for the existing account. Then sign out and sign back in; the account will show an Admin access page with a per-user AI access matrix. Do not promote an account you do not control.

These are manual access grants, not payment verification. Purchase history, automated plan/expiry enforcement, monthly usage counters, pricing controls, ad controls, and a separate admin sign-in page are not implemented yet. The grant table can store an expiry and monthly limit, but request counting does not currently enforce either.
