# Local workflow evaluations

Run the deterministic evaluation suite from `backend/`:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest evals -q
```

The suite separates three kinds of evidence:

- **Workflow A — discovery and criteria review:** mocked Gemini responses cover exact-source quote validation, unsupported numeric proposals, token usage capture, no-key behavior, and applicant-PII exclusion. Workflow JSON checks assert the scheduled discovery node feeds the review-queue node.
- **Workflow B — matching:** rule evaluations cover known-pass versus missing-information outcomes, employment normalization, and invalid input. Workflow JSON checks assert the upload webhook feeds SQL matching before notifications. Runtime matching jobs record their criteria fingerprint, attempts, result counts, and replay status.
- **Workflow C — notifications:** message construction asserts that delivery uses the controlled test recipient and excludes the applicant email. Workflow JSON checks assert delivery acknowledgement and capped page continuation. API claims prevent concurrent workflow runs from taking the same recipients; SMTP is retried and unacknowledged claims expire for retry.

The local test suite does not call Gemini or send email. Live acceptance should use one dedicated test CSV row and verify: Workflow B success; one Mailpit message; the applicant's match rows move from `pending` to `sent_to_local_test_inbox`; no message goes to the CSV address; and repeating the matching webhook returns the stored matching job without duplicate match rows. Discovery's live Gemini branch must be evaluated separately because it incurs an external model call; approve no criteria as part of an automated evaluation.

For a measured matching run, record batch ID, applicant count, fresh product count, candidate pairs, created rows, elapsed time, and PostgreSQL query plan. Do not compare a dry local run directly with a production throughput target.
