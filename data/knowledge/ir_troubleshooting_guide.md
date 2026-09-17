# IR Credit Request Troubleshooting Guide (SYNTHETIC — for portfolio use only)

All error codes and procedures in this document are invented for demonstration. They do not correspond to any real bureau specification.

## Request lifecycle
An IR credit request is created when a lender submits a borrower reference and a list of bureaus. IR fans out one integration call per bureau. IR status values: CREATED, IN_PROGRESS, COMPLETED, PARTIAL_FAILURE (at least one bureau succeeded and at least one failed), FAILED (all bureaus failed), RETRY_QUEUED.

## Retry policy
A request may be retried at most 3 times in total. Retries are a sensitive operation because each bureau pull may be billed and may be recorded on the consumer's file. Retries must be approved by an authorized IR operations user. Never retry a request whose error is marked non-retryable.

Concretely: if retry_count is 0, 1, or 2, one more retry is permitted. Only when retry_count is 3 must the request be escalated instead of retried.

## Error code reference

### TU-IDM-402 — TransUnion identifying information mismatch
Meaning: TransUnion could not match the borrower's name, address, or date of birth to a consumer file.
Retryable: Yes, but only after the borrower's identifying information has been verified with the lender.
Procedure: 1) Confirm the borrower reference on the request matches the lender's submission. 2) Ask the lender to verify name, current address, and date of birth. 3) After correction, retry the TransUnion pull only — do not re-pull Equifax if it already completed.

### EQ-IDM-402 — Equifax identifying information mismatch
Meaning and procedure identical to TU-IDM-402, applied to the Equifax pull.

### EQ-TMO-504 / TU-TMO-504 — Bureau gateway timeout
Meaning: The bureau did not respond within IR's 30-second timeout. Usually transient.
Retryable: Yes.
Procedure: 1) Check the bureau status page for a known outage. 2) If no outage, retry immediately. 3) If a second timeout occurs, wait 15 minutes before the next retry. 4) If retry_count is 3, escalate to the integrations team. If retry_count is 2, one final retry is still permitted — propose it and note that it is the last allowed attempt.

### EQ-FRZ-301 / TU-FRZ-301 — Consumer file frozen
Meaning: The consumer has placed a security freeze on their file. The bureau will not release a report.
Retryable: No. Retrying will fail again and may generate a billable inquiry.
Procedure: Notify the lender that the borrower must temporarily lift the freeze with the bureau. Do not retry until the lender confirms the freeze has been lifted.

### IR-VAL-100 — Request validation failure
Meaning: The lender's request was missing required fields (bureau list, borrower reference, or loan purpose).
Retryable: No. The lender must submit a corrected request.

## Pending status
A bureau pull in PENDING status for less than 5 minutes is normal. If PENDING exceeds 5 minutes with no error, treat it as a possible timeout and follow the timeout procedure after 30 minutes.

## Escalation
Escalate to the integrations team when: retry count is 3 or more, an error code is not in this guide, or the two bureaus fail with different kinds of error (for example a timeout on one and an identity mismatch on the other). Two bureaus failing with the same kind of error, such as both timing out, is not a reason to escalate on its own — follow that error's procedure.
