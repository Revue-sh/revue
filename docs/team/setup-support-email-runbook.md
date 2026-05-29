# Support Email Setup Runbook — support@revue.sh

**Ticket:** [REVUE-358](https://urukia.atlassian.net/browse/REVUE-358)

This runbook documents the exact steps to set up the `support@revue.sh` email address using **Cloudflare Email Routing** (inbound, free) and **Brevo SMTP relay** (outbound, free tier 300/day) wired into Gmail's "Send mail as" feature.

**Total cost:** $0/month  
**Setup time:** ~30 minutes  
**Skills required:** access to Cloudflare dashboard, Brevo account creation, Gmail settings

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ INBOUND: Customer → support@revue.sh                         │
│ ┌────────────────┐    ┌──────────────────┐    ┌─────────┐  │
│ │ External Email │ → │ Cloudflare Email  │ → │ Gmail   │  │
│ │ Sender         │    │ Routing (MX)      │    │ Inbox   │  │
│ └────────────────┘    └──────────────────┘    └─────────┘  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ OUTBOUND: support@revue.sh → Customer via Gmail "Send As"   │
│ ┌─────────┐    ┌──────────────┐    ┌──────────────┐         │
│ │ Gmail   │ → │ Brevo SMTP   │ → │ External     │         │
│ │ "Send   │    │ Relay        │    │ Recipient    │         │
│ │ mail as"│    │ (DKIM-signed)│    │              │         │
│ └─────────┘    └──────────────┘    └──────────────┘         │
└─────────────────────────────────────────────────────────────┘

All directions authenticate via SPF/DKIM/DMARC on revue.sh.
```

---

## Prerequisites

- **Cloudflare account** with `revue.sh` domain under your control (as authoritative nameserver)
- **Gmail account** (the destination inbox for forwarded mail + the "Send mail as" user)
- **Brevo account** (free tier; 300 transactional emails/day)
- **Access to Cloudflare DNS editor** (MX, TXT records)

---

## Part 1: Cloudflare Email Routing (Inbound)

Cloudflare Email Routing forwards all inbound mail to your existing Gmail inbox and automatically creates the necessary MX records.

### Step 1.1: Enable Email Routing in Cloudflare

1. Log into [Cloudflare Dashboard](https://dash.cloudflare.com)
2. Select the **revue.sh** domain
3. Go to **Email Routing** (left sidebar, under *Services*)
4. Click **Enable Email Routing** (or **Set Up Email Routing**)
5. Cloudflare will display the records it will add to DNS:
   - 3× **MX** records pointing to `route1.mx.cloudflare.net`, `route2.mx.cloudflare.net`, `route3.mx.cloudflare.net`
   - 1× **TXT** DKIM record (`cf2024-N._domainkey.revue.sh` with `v=DKIM1; …`) — Cloudflare's own DKIM key for Email Routing
   - 1× **TXT** SPF record on `revue.sh`: `v=spf1 include:_spf.mx.cloudflare.net ~all`

   > **Note on MX priorities:** Cloudflare auto-assigns its own priority numbers (you may see values like 3, 90, 95 rather than 10/20/30). This is normal — the exact numbers don't matter as long as all three `route{1,2,3}.mx.cloudflare.net` hosts are present.

6. Click **Add records** / **Done** to apply these records
7. Confirm — Cloudflare adds the MX, DKIM, and SPF records to your DNS

> **UI note (current Cloudflare layout):** the Email Routing page has tabs — *Overview, Activity Log, Routing rules, Destination Addresses, Destination Workers, Settings*. There is **no** "Custom addresses" section on the Overview page; custom addresses are created under the **Routing rules** tab, and forwarding inboxes are managed under the **Destination Addresses** tab. Do Step 1.2 (destination) **before** Step 1.3 (custom address), because a custom address can only point at an already-added destination.
>
> Right after enabling, **Status** may read *Syncing* and **DNS records** *Locked* — both normal. Syncing flips to *Active* within a couple of minutes; the lock just protects the email DNS records from accidental edits.

### Step 1.2: Add and Verify the Destination Inbox

1. Open the **Destination Addresses** tab
2. Click **Add destination** (or **Create destination address**)
3. Enter the Gmail inbox where forwarded mail should land (e.g., `your-email@gmail.com`)
4. Cloudflare sends a **verification email** to that inbox
5. Open the email in Gmail and click the **verification link**
6. The destination now shows **✅ Verified**

### Step 1.3: Create the support@revue.sh Custom Address

1. Open the **Routing rules** tab
2. Under **Custom addresses**, click **Create address**
3. **Custom address:** `support@revue.sh`
4. **Action:** *Send to an email*
5. **Destination:** select the verified Gmail inbox from Step 1.2
6. Click **Save**

**Test inbound mail:**  
Once Status shows *Active*, send a test email from an external account (personal Gmail, etc.) to `support@revue.sh`.  
Within 60 seconds, it should appear in your destination Gmail inbox.

---

## Part 2: Brevo Account & Domain Authentication (Outbound)

Brevo provides a free SMTP relay that signs outbound mail with DKIM for your domain. We'll authenticate `revue.sh` in Brevo, collect the DNS records, add them to Cloudflare, and then wire Gmail's "Send mail as" through Brevo's SMTP.

### Step 2.1: Create a Brevo Account

1. Go to [Brevo.com](https://www.brevo.com) and sign up (free account)
2. Complete the registration and verify your email
3. Log into the Brevo dashboard

### Step 2.2: Authenticate the revue.sh Domain in Brevo

1. In Brevo, go to **Settings** (account menu, top-right)
2. Select **Senders, Domains, IPs** > **Domains**
3. Click **Add a domain**
4. Enter `revue.sh` and click **Next**
5. Brevo will offer two options:
   - **Automatic authentication** (if you can log into your domain registrar from Brevo)
   - **Manual authentication** (copy/paste DNS records)

**For manual (recommended for control):**

1. Choose **Authenticate the domain yourself** (manual). Do **not** pick "automatically" — its DNS integration tends to add an SPF record, which would duplicate Cloudflare's existing SPF and break it. Click **Continue**.
2. Brevo displays **four** DNS records to add (no SPF — correct):
   - **Brevo code** — TXT, Name `@`, Content `brevo-code:<token>`
   - **DKIM 1** — CNAME, Name `brevo1._domainkey`, Content `b1.<domain-dashed>.dkim.brevo.com`
   - **DKIM 2** — CNAME, Name `brevo2._domainkey`, Content `b2.<domain-dashed>.dkim.brevo.com`
   - **DMARC** — TXT, Name `_dmarc`, Content `v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com`
3. **Note down all four records** (you'll add them to Cloudflare DNS next)
4. In Brevo, do NOT click **Authenticate / Verify** yet — wait until the records are in Cloudflare (step 2.3)

### Step 2.3: Add Brevo DNS Records to Cloudflare

> **⚠️ Do NOT add a second SPF record.** You already have one SPF TXT record from Cloudflare Email Routing (`v=spf1 include:_spf.mx.cloudflare.net ~all`). Brevo does **not** require an SPF change — it manages SPF internally on its own sending domain. Two SPF records on the same name break SPF entirely, so leave the existing Cloudflare SPF untouched and only add Brevo's **code**, **DKIM**, and **DMARC** records below.

1. Log back into [Cloudflare Dashboard](https://dash.cloudflare.com)
2. Select **revue.sh** domain
3. Go to **DNS** (left sidebar)
4. For each record Brevo provided:

   **Brevo code (TXT record):**
   - Name: `@` (the root `revue.sh`)
   - Type: `TXT`
   - Value: `brevo-code:<token>` (exactly as Brevo shows it)
   - Click **Save**
   - **Note:** you will now have *two* TXT records at `@` (the SPF one + this Brevo code). That is fine — multiple TXT records on the same name are allowed. The "only one" rule applies *only* to SPF-type records, and the Brevo code is not SPF.

   **DKIM 1 + DKIM 2 (two CNAME records):**
   - DKIM 1 → Name: `brevo1._domainkey`, Type: `CNAME`, Target: `b1.<domain-dashed>.dkim.brevo.com`
   - DKIM 2 → Name: `brevo2._domainkey`, Type: `CNAME`, Target: `b2.<domain-dashed>.dkim.brevo.com`
   - 🔴 **Proxy status: DNS only (grey cloud) for BOTH.** Cloudflare defaults new CNAMEs to *Proxied* (orange cloud) — click the cloud to turn it grey. A proxied DKIM CNAME makes Cloudflare answer with its own proxy target instead of resolving to Brevo, so DKIM verification fails.
   - Click **Save** for each

   **DMARC (TXT record):**
   - Name: `_dmarc`
   - Type: `TXT`
   - Value: use the value Brevo provides — `v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com` (aggregate reports go to Brevo, viewable in the dashboard). Starts at `p=none`; tighten to `p=quarantine` later after monitoring.
   - Click **Save**

### Step 2.4: Verify Domain Authentication in Brevo

1. Return to Brevo Settings > Domains
2. Find `revue.sh` in the list
3. Click **Verify this domain** (or **Authenticate**)
4. Brevo will check DNS and confirm:
   - ✅ Brevo code verified
   - ✅ DKIM verified
   - ✅ DMARC verified
5. Once all three show green checks, the domain is authenticated

### Step 2.5: Generate SMTP Credentials

1. In Brevo, go to **Settings** > **SMTP & API**
2. In the **SMTP** section, look for **SMTP credentials** or **SMTP key**
3. Click **Generate SMTP Key** if no key exists
4. Brevo will display:
   - **SMTP Server:** `smtp-relay.brevo.com`
   - **Port:** `587` (or `465` if you prefer SSL)
   - **Username:** [Your Brevo login email]
   - **SMTP Key:** [A long alphanumeric key — save this securely!]
5. **Note down these credentials** — you'll need them in Step 3

---

## Part 3: Configure Gmail "Send mail as" with Brevo SMTP

Now we'll set up Gmail to send from `support@revue.sh` using Brevo's SMTP relay.

### Step 3.1: Add "Send mail as" Address in Gmail

1. Log into Gmail in your browser
2. Go to **Settings** (gear icon, top-right) > **See all settings**
3. Select the **Accounts and Import** tab
4. Under **Send mail as**, click **Add another email address**
5. A popup will appear. Enter:
   - **Name:** `Revue Support` (or your choice)
   - **Email address:** `support@revue.sh`
   - Tick **Treat as an alias** (optional but recommended)
6. Click **Next Step**

### Step 3.2: Configure SMTP for the New Address

1. Gmail will now ask for SMTP settings
2. Enter the Brevo SMTP credentials from Step 2.5:
   - **SMTP Server:** `smtp-relay.brevo.com`
   - **Port:** `587`
   - **Username:** [Your Brevo login email from Step 2.5]
   - **Password:** [Your SMTP Key from Step 2.5]
   - **Secured connection using TLS:** ✅ Yes
3. Click **Add account** or **Next**
4. Gmail may ask to verify the address. If so, it will send a verification email to `support@revue.sh` — **check the forwarding Gmail inbox** for this email and click the verification link
5. Once verified, the address `support@revue.sh` will appear in your Gmail **From** dropdown

### Step 3.3: Test Sending from support@revue.sh

1. Compose a new email in Gmail
2. Click the **From** field (top-left of compose area)
3. Select `support@revue.sh` from the dropdown
4. Send a test email to a personal Gmail address (or any external email)
5. Check the received email:
   - **From header** should show `support@revue.sh` (not your personal Gmail)
   - **Authentication-Results header** should show `SPF: pass` and `DKIM: pass` with `d=revue.sh`
   - (To see raw email headers in Gmail, open the email, click **⋮ (More)** > **Show original**)

---

## Part 4: DNS Records Summary & Validation

### Complete DNS Record Set (to Record for Future Reference)

Once all steps are complete, your `revue.sh` DNS should contain:

```
MX Records (Cloudflare Email Routing — priorities auto-assigned by Cloudflare):
  route1.mx.cloudflare.net
  route2.mx.cloudflare.net
  route3.mx.cloudflare.net

SPF Record (TXT) — created by Cloudflare, leave as-is (do NOT add a second SPF):
  v=spf1 include:_spf.mx.cloudflare.net ~all
  (Brevo needs no SPF entry — it manages SPF on its own sending domain)

DKIM Records (TXT) — TWO are expected, and that's correct:
  cf2024-N._domainkey.revue.sh   → Cloudflare's DKIM key (auto-added with Email Routing)
  <brevo-selector>._domainkey.revue.sh → Brevo's DKIM (CNAME or TXT, from Brevo dashboard)

DMARC Record (TXT):
  v=DMARC1; p=none; rua=mailto:dmarc@revue.sh; ruf=mailto:dmarc@revue.sh
  (Start with p=none, tighten to p=quarantine later after monitoring)

Brevo Code (TXT):
  _brevo-code.revue.sh → [Brevo-provided code value]
```

**Record these exact values in REVUE-358 ticket Description → Notes section** so the setup is reproducible and auditable.

---

## Part 5: Validation & Testing

> **Validation complete (2026-05-29):** Tests 5.1–5.5 ✅ ALL PASSED (mail-tester 8/10; bounce returns NDR). Only optional post-launch 2FA hardening remains.

### Test 5.1: Inbound Delivery

**Send:** External email → `support@revue.sh`  
**Expect:** Arrives in forwarding Gmail inbox within 60 seconds  
**Pass Criteria:** Email received, no bounces  
**Status:** ✅ **PASSED** (2026-05-29) — external send to `support@revue.sh` delivered to the forwarding Gmail inbox in under 60s.

### Test 5.2: Outbound Authentication

**Send:** From Gmail "Send mail as `support@revue.sh`" → external Gmail  
**Expect:** Recipient's Gmail shows From as `support@revue.sh`  
**Inspect headers:**
- `From: support@revue.sh`
- `Authentication-Results: ... spf=pass ... dkim=pass (d=revue.sh) ...`

**Pass Criteria:** Both headers correct  
**Status:** ✅ **PASSED** (2026-05-29) — verified via Gmail "Show original" on a message sent FROM `support@revue.sh`:
- `From: Revue Support <support@revue.sh>` ✅
- `SPF: PASS` (sending IP `77.32.148.27`) ✅
- `DKIM: PASS` with domain `revue.sh` ✅ (domain-aligned, not brevo.com)
- Delivered after 14 seconds.

### Test 5.3: DMARC Alignment

**Action:** Send from `support@revue.sh` via Gmail, inspect the received email headers  
**Expect:** `dmarc=pass` with aligned DKIM signature on `revue.sh`  

**Pass Criteria:** DMARC alignment confirmed (no quarantine/reject from recipient's DMARC policy)  
**Status:** ✅ **PASSED** (2026-05-29) — same message showed `DMARC: PASS` with aligned DKIM on `revue.sh`.

### Test 5.4: Mail Reputation Score

**Action:** Use [mail-tester.com](https://www.mail-tester.com/)
1. Go to mail-tester.com
2. Copy the provided test email address (e.g., `test-abc123@mail-tester.com`)
3. From Gmail using "Send mail as `support@revue.sh`", send an email to that test address
4. Return to mail-tester.com and click **Check your score**
5. Review the score and any flagged issues

**Pass Criteria:** Score ≥ 8/10  
**Status:** ✅ **PASSED** (2026-05-29) — scored **8/10** with a realistic, text-rich support reply (~230 words).

> **Deliverability note:** mail-tester must be run with a *normal-length* message. A near-empty body (e.g. "Test") scores ~7.1 because Brevo's mandatory open-tracking pixel makes the email look "image-only" (`HTML_IMAGE_ONLY`). Brevo's free SMTP does **not** allow disabling tracking, so the pixel-related penalties (`URI_NOVOWEL` on the tracking link, image-without-`alt`) are unavoidable — but a real support-length message easily offsets them and clears 8/10. Underlying authentication is perfect regardless (SPF/DKIM `d=revue.sh`/DMARC all pass, IP on Mailspike good-sender whitelist).

### Test 5.5: Bounce Handling

**Send:** Email to a non-existent address like `nobody@revue.sh`  
**Expect:** A non-delivery report (NDR) is returned to the sender within minutes  
**Inspect:** Confirm the NDR contains a clear error message (e.g., "user unknown")

**Pass Criteria:** Bounce received, no silent drop  
**Status:** ✅ **PASSED** (2026-05-29) — send to `nobody@revue.sh` returned an NDR from `mailer-daemon@googlemail.com`: *"Address not found … 550 5.1.1 Address does not exist."* No silent drop. (Cloudflare catch-all is off, so unmatched recipients are correctly rejected.)

---

## Troubleshooting

### Inbound Mail Not Arriving

1. **Check Cloudflare Email Routing status:**
   - In Cloudflare dashboard, confirm Email Routing shows **Enabled**
   - Confirm custom address `support@revue.sh` shows **Active** or **Verified**

2. **Verify destination address was verified:**
   - In Cloudflare Email Routing, check the destination inbox for verification email from Cloudflare
   - If not found, re-send the verification email

3. **Check MX records in DNS:**
   - In Cloudflare DNS, confirm three Cloudflare MX records are present and priority is correct (10, 20, 30)

### Outbound Mail Fails DKIM / SPF

1. **Check DNS records in Cloudflare:**
   - Confirm all three Brevo records (Brevo code, DKIM CNAME, DMARC TXT) are present
   - Verify no duplicate MX or SPF records exist (multiple SPF records cause failures)

2. **Check Gmail SMTP credentials:**
   - Confirm in Gmail Settings > Accounts and Import > Send mail as, the SMTP settings show `smtp-relay.brevo.com` (not `smtp.gmail.com`)
   - Verify the SMTP key has not expired or been revoked in Brevo

3. **Check Brevo domain status:**
   - In Brevo Settings > Domains, confirm `revue.sh` shows all three records authenticated with green checks

### Recipient's DMARC Policy Blocks Mail

If the recipient uses a strict DMARC policy (`p=quarantine` or `p=reject`) and your forwarded mail from Cloudflare is failing DMARC alignment:

1. Confirm your own DMARC policy is `p=none` (not `p=quarantine` or `p=reject`) — see Part 4
2. Monitor incoming DMARC reports (sent to `dmarc@revue.sh`) to identify which domains have issues
3. Once stable, you can later tighten your policy — but start permissive at launch

---

## Hardening (Post-Launch)

Once the setup is live and stable, apply these security measures:

1. **Enable 2FA on all accounts:**
   - Cloudflare account: Settings > Authentication > Two-factor authentication
   - Brevo account: Settings > Security
   - Gmail account: myaccount.google.com > Security > 2-Step Verification

2. **Rotate SMTP key regularly:**
   - In Brevo, generate a new SMTP key every 90 days
   - Update Gmail "Send mail as" settings with the new key

3. **Monitor DMARC reports:**
   - A DMARC policy with `rua=mailto:dmarc@revue.sh` sends aggregate reports daily
   - Check for unexpected mail sources or authentication failures

4. **Archive old DMARC policies:**
   - After 30 days at `p=none`, review reports and consider tightening to `p=quarantine`
   - Monitor for 14 more days before moving to `p=reject`

---

## Summary Checklist

- [x] Cloudflare Email Routing enabled; MX records present
- [x] Custom address `support@revue.sh` created and verified in Cloudflare
- [x] Brevo account created; domain `revue.sh` authenticated (all records green/match)
- [x] Brevo DNS records (code, DKIM 1, DKIM 2, DMARC) added to Cloudflare DNS
- [x] SMTP credentials generated in Brevo
- [x] Gmail "Send mail as `support@revue.sh`" configured with Brevo SMTP
- [x] Inbound test (5.1): external email → `support@revue.sh` → Gmail inbox ✅
- [x] Outbound test (5.2 + 5.3): Gmail "Send as `support@revue.sh`" → external email; From + SPF + DKIM (`d=revue.sh`) + DMARC all PASS ✅
- [x] mail-tester.com score ≥ 8/10 (Test 5.4) — scored 8/10 with a text-rich message ✅
- [x] Bounce test: email to `nobody@revue.sh` returns NDR (Test 5.5) — 550 5.1.1 Address does not exist ✅
- [x] Final DNS records documented in REVUE-358 ticket
- [ ] 2FA enabled on Cloudflare, Brevo, Gmail — pending
