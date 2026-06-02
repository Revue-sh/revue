# Privacy Policy

**Last updated: June 2, 2026**

<!-- [PENDING LEGAL REVIEW] — This draft accurately reflects actual data flows. Counsel sign-off required before public launch. -->
<!-- [PENDING REGISTRATION] — "Token Labs Ltd" is not yet incorporated at Companies House. Confirm the registered name + company number before launch. -->

## Introduction

This Privacy Policy explains how Token Labs Ltd ("Token Labs," "Company," "we," "us," or "our"), the operator of the Revue service, collects, uses, discloses, and safeguards information when you use our website (revue.sh), service, and code review skill ("Service"). Token Labs Ltd is a company registered in England & Wales.

Please read this Privacy Policy carefully. If you do not agree with our policies and practices, please do not use the Service. By using the Service, you acknowledge that you have read, understood, and agree to be bound by all the terms of this Privacy Policy.

## 1. Information We Collect

### 1.1 Information You Provide Directly

- **Account Creation:** Email address, name (optional), and password or authentication credentials (e.g., GitHub/GitLab/Bitbucket OAuth token).
- **Subscription & Billing:** Payment information (name, billing address, ZIP code). **Payment card details are processed by Stripe and are not stored by Revue.**
- **Support & enquiries:** When you contact us (e.g. [legal@revue.sh](mailto:legal@revue.sh)), we collect your email, message content, and any attachments.
- **Source Code Submission:** When you use the Service to review code, metadata about the code change (file names, line counts, repository information) may be logged.

### 1.2 Information Collected Automatically

- **Usage Data:** When you activate your licence or submit a code review, we log:
    - Email address used for activation.
    - Timestamp of review submission.
    - Number of reviews performed (for tier tracking).
    - Repository and platform metadata (GitHub/GitLab/Bitbucket account info, repo name).
    - Error logs and diagnostic data.
- **IP Address & Request Logs:** For rate-limiting and abuse prevention on our API endpoints, we temporarily log:
    - Client IP address.
    - Request timestamp and endpoint.
    - Response status code.
    - Retention: 30 days for rate-limit enforcement.
- **Licence & JWT Data:** Your licence key and signed JWT token are logged for verification purposes. JWTs are not stored in plaintext; only the verification result is retained.

### 1.3 Local Execution

When you run Revue locally, code review runs **entirely within your own environment** (your AI coding agent, e.g. Claude Code). Revue does not receive, store, or process your source code through our servers. The only data transmitted to Revue is:

- Activation metadata (email, licence key, timestamp).
- Aggregated usage counts (number of reviews performed).

Your code **never leaves your machine** when you run Revue locally.

## 2. Purpose and Legal Basis for Processing

We process your information for:

| Purpose | Legal Basis |
|---------|------------|
| **Account management** — create accounts, authenticate users | Contractual necessity |
| **Service delivery** — process code reviews, manage subscriptions | Contractual necessity |
| **Billing & payments** — invoice, detect fraud | Contractual necessity & legal obligation |
| **Tier enforcement** — track Free Tier usage (25 reviews/month soft cap) | Contractual necessity |
| **Rate-limiting & security** — prevent abuse, block malicious access | Legitimate interest in network security |
| **Email communication** — transactional emails (activation, billing), support responses | Contractual necessity |
| **Analytics & improvement** — aggregate usage trends to improve the Service | Legitimate interest |

### Legal Basis (UK GDPR, EU GDPR, and Equivalent Laws)

For users in the UK, EU, or regions with equivalent data protection laws:

- **Contractual Necessity:** Processing is required to perform our contract with you (e.g., service delivery, billing).
- **Legitimate Interest:** We have a compelling business interest in securing our systems and improving service quality, which we balance against your privacy rights.
- **Legal Obligation:** Processing is required by tax, anti-fraud, or law enforcement obligations.

## 3. Data Processors and Sub-processors

We share your information with the following sub-processors:

| Sub-processor | Purpose | Location |
|---|---|---|
| **Stripe, Inc.** | Payment processing (card authorization, billing) | USA |
| **Brevo SAS** | Transactional email (activation emails, billing receipts) | France / EU |
| **Cloudflare, Inc.** | Email routing & DDoS protection | USA |
| **Fly.io** | Web hosting and infrastructure | USA |

**Important:** When you run Revue locally, your code is sent directly from your machine to the AI inference provider you configure (a "bring your own key" model). Revue does not proxy, receive, or relay this data. You are responsible for reviewing and agreeing to the privacy terms of the AI provider you choose.

All sub-processors are contractually obligated to handle your data in accordance with applicable law and this Privacy Policy.

## 4. Data Retention

We keep personal data only for as long as necessary for the purposes set out in this Policy (the **storage-limitation principle** under UK GDPR), after which it is deleted or anonymized:

| Data Type | Retention Period | Purpose |
|---|---|---|
| **Account & licence** | Until account deletion or licence expiry | Service delivery |
| **Usage logs** (review counts, timestamps) | 24 months | Tier enforcement, analytics |
| **API logs** (IP, endpoint, status) | 30 days | Rate-limiting, abuse prevention |
| **Support emails** | As long as necessary, up to 6 years (Limitation Act 1980) or per legal hold | Support history, dispute resolution |
| **Email addresses** (for transactional email) | For duration of account + 12 months | Compliance, re-engagement |
| **JWT tokens** (signed signatures) | 24 hours | Offline licence verification |

You may request deletion of your account and associated data at any time via [legal@revue.sh](mailto:legal@revue.sh). We will delete or anonymize your data within 30 days, except where legal obligation or legitimate business need requires longer retention.

## 5. Your Rights

### UK, EU/EEA, and Equivalent Jurisdictions (UK GDPR, EU GDPR, ePrivacy)

You have the right to:

- **Access:** Request a copy of your personal data. Contact [legal@revue.sh](mailto:legal@revue.sh) to exercise this right.
- **Rectification:** Correct inaccurate or incomplete data.
- **Erasure** ("Right to be Forgotten"): Request deletion of your data, subject to legal obligations.
- **Restriction of Processing:** Request that we limit how we use your data.
- **Portability:** Request your data in a portable format (e.g., CSV) suitable for transfer to another service.
- **Objection:** Object to processing based on legitimate interest (e.g., analytics).
- **Lodge a Complaint:** Contact your local data protection authority if you believe we have violated your rights. In the UK, this is the Information Commissioner's Office (ICO) at [ico.org.uk](https://ico.org.uk).

To exercise any of these rights, email [legal@revue.sh](mailto:legal@revue.sh) with the subject line "Data Subject Request."

### Other Jurisdictions

Depending on your location, you may have rights to:

- Know what personal data is collected.
- Delete personal data (subject to legal obligations).
- Opt-out of certain uses (e.g., marketing communications).

### Right to Opt-Out of Marketing

We do not send unsolicited marketing emails. You may opt-out of transactional emails (activation, billing) by deleting your account. You cannot opt-out of essential account and billing communications while your account is active.

## 6. Data Security

We implement technical and organizational measures to protect your information:

- **Encryption:** Data in transit is encrypted using TLS 1.2 or later.
- **Access Control:** Only authorized employees have access to customer data, and only for legitimate business purposes.
- **Incident Response:** We monitor for unauthorized access. In the event of a personal-data breach, we will notify the ICO within 72 hours where required, and affected users without undue delay where the breach is likely to result in a high risk to their rights.

However, no security measure is 100% effective. We encourage you to use strong passwords and enable two-factor authentication on your source control accounts.

## 7. Children's Privacy / Age Requirements

The Service is not directed to children. You must be at least 18 years old to create an account, accept the Terms of Service, and act as the contracting party.

Revue does not knowingly collect personal data from children under 13. Where a person aged 13–17 uses the Service, they may do so only through an account held by a parent, guardian, or other adult aged 18 or over, who is the account holder and payer, who accepts the Terms of Service, and who is responsible for all use of the account and all charges incurred.

If we learn we have collected personal data from a child under 13 without appropriate consent, we will delete it. To raise a concern, contact [legal@revue.sh](mailto:legal@revue.sh).

## 8. International Data Transfers

Your information may be transferred to and stored in countries other than your country of residence, including the United States. These countries may not have the same data protection laws as your home country. By using the Service, you consent to such transfers.

If you are a resident of the UK or EU/EEA, we rely on appropriate legal mechanisms (UK International Data Transfer Agreement, Standard Contractual Clauses, adequacy decisions, or your consent) to lawfully transfer your data internationally.

## 9. Third-Party Links

The Service may contain links to third-party websites (GitHub, GitLab, Bitbucket, Stripe). We are not responsible for their privacy practices. Please review their privacy policies before sharing information with them.

## 10. Changes to This Privacy Policy

We may update this Privacy Policy at any time. The "Last updated" date at the top reflects the most recent version. Continued use of the Service after updates constitutes acceptance of the new Privacy Policy.

Material changes (e.g., expanded data processing) will be notified via email at least 30 days before taking effect.

## 11. Contact Us

If you have questions about this Privacy Policy or wish to exercise your data rights:

**Email:** [legal@revue.sh](mailto:legal@revue.sh)

We will respond to data subject requests within 30 days (or as required by law).
