# Phase 4: Users, Ticks, and Incident Reporting Design

- **Date:** 2026-09-27
- **Status:** Draft — decisions recorded 2026-09-27 (rev 3)
- **Depends on:** Phase 1 (Neon roles via SQL, Alembic, CI), Phase 2 (`2026-09-27-phase2-data-platform-design.md`: accident tables, provenance, dedupe), and Phase 3 (`2026-09-27-phase3-model-v2-design.md`: tick exposure, the "≥50 newly reviewed incidents" retrain trigger, auto-published promotion, five route type groups, the discipline-split public catalog).

## Goal

Add accounts, in-app ticks (long-term exposure), and user incident reports triaged by Jev (TypeSafe AI). The risk model stays in-house. Accuracy of safety info comes first. No single user, and no coordinated group of users, can sharply move a route's score without corroboration or a human.

**Non-goals:** Jev outside report triage, publishing user narratives, photos in v1, experience personalization before the data supports it, and inferring climber experience by matching accident reports or ticks to people by name (declined: reports are mostly anonymized, matching would re-identify injured or deceased climbers, and ticks are sparse; see Phase 3 M13).

**Route catalog:** every route and area reference in this phase (ticks, report location, candidate lists, home area) uses Phase 2's public catalog (`canonical_routes`/`canonical_areas`/`objectives`), per Phase 3 M11: OpenBeta (CC0) for rock, MP route facts (name, grade, location, type) for ice and mixed, and the Objective layer for mountaineering. No other MP data (prose, photos, comments, rock data, ticks) is ever shown to users or sent to Jev. Where the catalog is still thin, the report form's free-text location is expected; such reports feed the Phase 2 coverage strategy, and thin-coverage routes show the Phase 3 coverage warning.

## Jev facts (researched 2026-09-27)

**Verified in vendor docs:**

- **API:** `POST https://api.typesafe.ai/v1/systemone` with a Bearer key. Keys come from console.typesafe.ai. The Python SDK is `typesafe-sdk` (`AsyncTypeSafeClient`, Python ≥3.10) ([quickstart](https://docs.typesafe.ai/introduction/quickstart)).
- **Request:** `{state, model, questions}`. `state` is text only: a string, a JSON object, or an array.
- **Question types:** `choice` (≤255 options; `probabilities` + `confidence`), `score` (2–10 levels), `noul` (0–1 probability, no confidence field) ([choice](https://docs.typesafe.ai/primitives/choice), [api](https://docs.typesafe.ai/api.md)).
- **No span, date, or string extraction.** Jev only answers the questions we define.
- **Versions:** the current model is `jev-1.13.0`. The `jev-latest` and `jev-preview` aliases move silently. The versioned ID can be pinned and is echoed in each response.
- **Limits:** 64k tokens per request, 32k for state plus the longest question.
- **Price:** $0.042/M input tokens, output free ([models](https://docs.typesafe.ai/models)).
- **Stated weaknesses:** dates, numbers, context rot. The docs say it "does not treat [data] as hostile by default", so injected instructions can work ([jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)).
- **Confidence:** derived from how spread out the probabilities are. The vendor recommends domain-specific, conservative thresholds and makes no universal calibration guarantee ([confidence](https://docs.typesafe.ai/confidence)).
- **Errors:** 401, 422, 429, and 529, handled with backoff. Rate limits are unpublished ("adjusting dynamically").
- **Privacy:** no training on inputs; US-hosted; retention "as long as reasonably necessary"; zero data retention (ZDR) enterprise-only via sales; DPA breach notice ≤72 h ([privacy](https://typesafe.ai/legal/privacy-policy), [DPA](https://typesafe.ai/legal/data-processing)).
- **Access:** waitlisted early access. [MarkTechPost (2026-09-19)](https://www.marktechpost.com/2026/09/19/typesafe-ai-releases-jev/) says it started 2026-09-15, but the vendor [blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev) is dated 2026-09-27, so the two dates conflict.
- **Also consulted:** [Willison](https://simonwillison.net/2026/Sep/21/jev/), [LangChain](https://www.langchain.com/blog/building-a-harness-with-jev), [awesome-jev](https://github.com/cobanov/awesome-jev).

**Not verified:** numeric rate limits, SLA, retention period, subprocessors, deprecation notice, ZDR on early access, calibration on our domain. **jevai.net** lists $0.084/M and a "DefAPI" trial, contradicting the vendor; treat as unofficial.

**Consequence:** date and location come from the form and deterministic code. Jev only classifies, and only picks among candidates we generate.

**Verify before build (M4.0):**

1. Early-access key obtained.
2. Rate limits, SLA, and retention period confirmed in writing, and ZDR requested.
3. How long pinned `jev-1.13.0` stays callable after a new release.
4. SDK ≥0.7 interface checked (the 2026-09-18 release made a breaking switch to Pydantic).
5. No request-logging option that is on by default.
6. Whether a vendor-side spend cap exists.

## A. Accounts and profiles

**Self-hosted auth (DECIDED):** FastAPI, email and password, with mandatory email verification. $0 beyond email, fully portable, and no PII held by an auth vendor; the cost is that we own the attack surface, so the authz and security test suites (H) are mandatory.

- **Passwords:** argon2id via `argon2-cffi`. New passwords are checked against the HIBP k-anonymity range API.
- **Sessions:** opaque and server-side, so revocable (JWT rejected for that reason). 256-bit cookie, `HttpOnly; Secure; SameSite=Lax`, stored as SHA-256 only; 14-day idle / 60-day absolute expiry; rotated on login and role change.
- **CSRF:** a double-submit `X-CSRF-Token` on every mutating request.
- **CORS:** tighten `allow_methods=["*"]` in `main.py`.
- **Email (DECIDED): Resend**, free tier, behind a small `EmailSender` protocol (`async send(to, template, params) -> MessageId`) so the provider can be swapped without touching auth code. `RESEND_API_KEY` lives only in Railway env. CI uses an in-memory `EmailSender`.
- **Later:** passkeys (`py_webauthn`), then Google OAuth.

**Profile:**

- **Experience per discipline** (`sport | trad | alpine | ice | mixed | boulder`; the first five match the Phase 3 type groups, `boulder` is profile-only because v2 does not score bouldering):
  - Level: `none | beginner | intermediate | advanced | expert`.
  - Optional max grade, validated per system (YDS, V, WI, alpine).
  - Years bucket: `<1 | 1–3 | 3–10 | 10+`.
- **Certifications:** enum (AMGA, IFMGA, WFA/WFR, AIARE, Other), labeled "self-reported, unverified".
- **Home area:** optional catalog area ID (`canonical_areas`), never coordinates.
- Self-reported experience is the only source of per-person experience; it is used only in aggregate (F).

**Privacy and abuse:**

- **Minimized PII:** email is the only required field. No name, no birthdate, no raw IP. IPs are HMAC'd with a rotating key and kept 30 days.
- **Export:** `GET /me/export`.
- **Delete:** `DELETE /me` hard-deletes the profile and ticks. Reports are detached from the user, raw text is purged, and accepted facts are kept under the submission license.
- **Age (DECIDED): 15+ attestation.** This is above the COPPA threshold (13). It is acceptable because there is no public forum or profile, and no names are recorded or published. PII stays minimized as above. Future check (not blocking): if EU users are ever targeted, GDPR digital-consent ages (13–16 by member state) apply and the age gate must be revisited.
- **Bot defense (DECIDED):** Cloudflare Turnstile on signup, reports, and takedowns. Disposable email domains are blocked.

**Rate limiting and DDoS protection (REQUIRED):**

- **App layer (DECIDED):** per-IP-hash and per-account fixed-window limits, stored in the existing Redis (Celery broker) with the Postgres `rate_limits` table as fallback if Redis is unavailable. Limits: signup 5/IP-hash/h; login 10/account and 30/IP-hash per 15 min; emails 3/account/h; incident reports 3/account/24 h, 10/account/30 days, and 10/IP-hash/24 h; takedowns 5/IP-hash/24 h. Over-limit returns 429 with `Retry-After`. Turnstile is verified on every submission.
- **Infrastructure layer (DECIDED): Cloudflare free proxy** in front of `safeascent.us`. DNS moves from Porkbun to Cloudflare nameservers; Porkbun stays the registrar. This adds edge DDoS absorption, one free rate-limiting rule on `POST /api/v1/incident-reports` and `/auth/*`, and bot filtering. The origin then accepts traffic only via Cloudflare, and the app reads the client IP from `CF-Connecting-IP` only when the request came from a Cloudflare IP range. The Railway `www` custom domain and nginx 301 (Phase 1 D7) keep working behind the proxy (SSL mode Full (strict)).
- **Origin hardening (always on, behind Cloudflare):** request body limits (reports 16 KB, other JSON 64 KB, enforced before parsing), slow-client timeouts at nginx (`client_header_timeout` and `client_body_timeout` 10 s) and uvicorn (`--timeout-keep-alive 5`, `--limit-concurrency` sized to the worker), and the Turnstile check before any expensive work.
- **Roles:** `user | reviewer | admin`, enforced as FastAPI dependencies. This replaces the Phase 0 admin gate.

## B. Ticks

**Columns:** `ticks(id, user_id, route_id, climbed_on, style, outcome, pitches, party_size, notes, visibility, created_at)`.

- `route_id`: a `canonical_routes` route (OpenBeta, `mp_facts` ice/mixed, or curated objective route; M11).
- `climbed_on`: not in the future, not before 1950.
- `style`: `lead | follow | toprope | solo | aid | boulder`.
- `outcome`: `send | attempt | bail`.
- `notes`: at most 1,000 chars, private.
- `visibility`: `private` by default.

**UX:** a "Log climb" button on route detail and a "My ticks" list.

**Privacy:** the model sees only aggregates (distinct user-route-days per Phase 3 cell). Per-user ticks are public only with per-tick opt-in. Public aggregates hide cells with fewer than 5 users.

**Anti-spam:** ≤30 ticks/day and one per user-route-day; only verified accounts ≥7 days old count toward exposure; ≤20 user-route-days per user per cell-month; >100 ticks in 24 h freezes the account's contribution pending review.

## C. Incident reporting pipeline

### C1. Form (`POST /incident-reports`, verified users; Pydantic at the boundary)

- **Date:** `event_date` plus `date_precision` (`day | month | year`).
- **Location:** a catalog route, area or objective picked from search, or free text up to 200 chars. Optional coordinates are rounded to 3 decimals.
- **Self-reported `incident_type`:** lead fall, follow/toprope fall, rappel error, lowering/belay error, anchor/pro failure, rockfall, icefall, avalanche, weather/exposure, other.
- **Severity:** `none | minor | serious | fatal | unknown`.
- **Weather-related:** `yes | no | unknown`.
- **Experience of the people involved:** optional bucket (years climbing, self-described level), not linked to the profile. It feeds the same structured experience fact Phase 2a extracts from accident records.
- **Relationship to the event:** `involved | witness | secondhand`.
- **Narrative:** 50–4,000 chars, NFC-normalized, control characters stripped.
- **Consent:** license and ToS acceptance.

**No photos in v1.** Photos bring EXIF GPS, victim images, CSAM moderation, and storage cost, and Jev is text-only.

**Limits:** 3 reports per 24 h and 10 per 30 days.

### C2. Stages

These run as Celery tasks. Submitting never waits on them.

1. **Deterministic validation and abuse filters.**
   - Checks: date between 1900 and today, the route exists, coordinates fall in US bounds, and the Turnstile token is valid. Hard failures return 422 synchronously.
   - Stored signals: link count, cross-user text-hash repeats, account age, per-area bursts.
2. **PII redaction before any third-party call.**
   - Presidio with spaCy `en_core_web_sm`, run in the worker, replaces PERSON, PHONE, EMAIL, URL, and @handles with typed placeholders.
   - Nearby route and area names are allowlisted so they survive redaction.
   - The raw narrative goes to `incident_report_raw`. Only reviewer code paths can read it, the `triage_worker` DB role never can, and it is purged 180 days after the decision.
3. **Triage via `IncidentTriager` (D1).**
   - **Sent:** the redacted narrative, the form's type, severity, and weather fields, the US state, and our candidate lists.
   - **Never sent:** user ID, email, IP, exact date, coordinates, raw text.
   - User text only ever appears in `state.report_text`, never in `instructions`.

| Key | Type | Content |
|---|---|---|
| `is_real_incident` | noul | Describes an actual event |
| `is_climbing` | noul | Technical climbing, bouldering, or alpine (not hiking/skiing) |
| `incident_type` | choice | Taxonomy |
| `severity` | choice | none/minor/serious/fatal |
| `weather_related` | noul | Weather contributed |
| `route_match` | choice | ≤20 catalog candidates (displayable fact fields only: name, grade, type, area; pg_trgm name match plus 10 km radius), plus `none_of_these` |
| `duplicate_of` | choice | Accidents within ±3 days and 5 km as structured summaries (never source narratives), plus `new_incident` |
| `consistent_with_form` | noul | Narrative agrees with the form |
| `addresses_system` | noul | Text contains instructions aimed at software or reviewers |

   Jev is never asked about dates, because dates are a documented weakness. `year` precision older than 2 years goes to a human.

4. **Routing.** Raw outputs pass through our isotonic calibration, fit on the gold set per pinned version. For a noul, confidence is defined as `|2p−1|`.
   - **Auto-stage** into `reviewed_incidents` (`status=staged`, `source=user`) only when: real and climbing ≥0.95; type and severity confidence ≥0.9 and matching the form; route ≥0.9 (or a catalog route was picked); duplicate ≥0.9; `addresses_system` <0.2; no abuse signals; account ≥7 days old; severity not fatal.
   - **Auto-reject** only when real or climbing is ≤0.05 **and** a deterministic spam signal fired.
     - The submitter can appeal with one click, and the appeal goes to a human.
     - Reviewers sample 10% of auto-rejects each week. If the false-reject rate exceeds 2%, it alerts.
   - **Everything else** goes to the human queue, ordered by severity, then age.
   - **Always human:** fatal reports, leftover person placeholders or names, `addresses_system` ≥0.2, appeals, the first report in an area with no prior incidents, and brigading clusters.

   **"Automated review" means** Jev plus rules may *stage* or *reject*. It never publishes, never edits an existing accident, and never grants full model weight.
5. **Duplicate and cross-source match.** The deterministic Phase 2 R5 rule (±2 days, ≤5 km, scored with the same auto-decide bands) runs first. Jev's `duplicate_of` only breaks ties. A report that matches an AAC, CAIC, or NPS record is linked as corroboration, not added as a new incident.
6. **Promotion.** This uses the owner-veto pattern: a daily digest of staged items, a 72 h veto window, then promotion.
   - Promoted reports land in the Phase 2 `accidents` table with `source='user_report'` and provenance (`report_id`, `triage_result_id`, `verification = auto | human | corroborated`).
   - Model weight: `auto` 0.5; `human` and `corroborated` 1.0.
   - Each promotion counts toward Phase 3's ≥50 retrain trigger at its weight.

### C3. Poisoning and brigading defenses

- **Weight caps:** uncorroborated user weight is at most 1.0 per Phase 3 cell per month, and at most 25% of a cell's training incidents.
- **Cluster rule:** 3 or more reports on one area within 7 days, from accounts younger than 30 days or sharing an IP-hash /24, send the whole cluster to the human queue and freeze its promotion.
- **Reports only add evidence.** Risk goes down only through human takedowns or reviewer edits.
- **Retrain guard (automatic, no hold):** before fitting, any cell where uncorroborated user reports make up at least 50% of its new incidents and would move a route's percentile by more than 20 points has those reports' weight set to 0 for that run. The version then goes through the Phase 3 gate and auto-publishes as normal, and the affected cells are listed in the M9 promotion issue; the owner's recourse is `/rollback` or corroborating the reports, which restores their weight at the next retrain.

## D. Jev safety and security protocol

1. **Abstraction:**

   ```python
   class IncidentTriager(Protocol):
       name: str
       async def triage(self, req: TriageRequest) -> TriageResult: ...
   ```

   There are three implementations:
   - `JevTriager`: pinned `model="jev-1.13.0"`.
   - `LocalTriager`: in-house TF-IDF plus logistic regression with isotonic calibration, trained on the gold set. It only prioritizes the queue and never auto-stages.
   - `ManualTriager`.

   **Kill switch:** `app_settings.triage_mode` (`jev | local | manual`). An admin can change it with no redeploy.
2. **Injection containment:** user text is data only. Responses are Pydantic-validated against the question set; unknown keys or options send the report to the queue. Output only writes `report_triage_results` and `routing`. The `triage_worker` DB role can only SELECT redacted report columns and candidate views and INSERT triage results. Adversarial cases are in the gold set.
3. **Version pinning:** if the response's `model` differs from the pinned version, results are recorded and the mode falls back to `local` with an admin alert. It stays there until a re-evaluation passes.
4. **Audit:** per call, SHA-256 of canonical request and response, model ID, latency, tokens, and the redacted request and answers in `report_triage_results`, plus an `audit_log` row.
5. **Keys and cost:** `TYPESAFE_API_KEY` only on the Railway worker, rotated quarterly or on suspicion. `JEV_MONTHLY_TOKEN_CAP` defaults to 50M (~$2.10); hitting it switches to `local`. Client-side limit 2 req/s.
6. **Outage:** 429/529/5xx/timeouts retry with exponential backoff (≤6 tries within 1 h), then queue. Submissions always succeed.
7. **Vendor risk:** DPA signed before real data is sent; redaction stays even with ZDR; if Jev is deprecated, switch to `local`/`manual` with no intake loss.
8. **Eval harness** (`uv run python -m app.triage.eval`):
   - **Gold set:** ≥600 positives templated from *structured fields* of existing accidents (no copyrighted AAC narrative text); ≥50 owner-written realistic reports; ≥250 negatives (hiking, skiing, gym, jokes, ads); ≥100 adversarial (injection, contradictory form, brigading).
   - **Gate before auto-stage is enabled:** auto-staged precision ≥0.98 (real, climbing, correct type and route); ECE ≤0.05; zero adversarial auto-staged; false-reject ≤2%.
   - **Re-evaluation:** monthly, on any model change, and on any threshold change. Results go to `docs/triage-eval/`.
9. **Monitoring:** routing mix, reviewer override rate on staged items (alert >5%), cost, errors, confidence PSI (alert >0.25).

## E. Moderation and legal

- **Submitter terms:** good faith; not an emergency channel ("call 911"); SafeAscent may edit or decline; Phase 3 disclaimer applies.
- **Licensing (DECIDED):** submitters grant **CC0 on factual fields** and an internal-only license on the narrative. Narratives are never published or redistributed.
- **Sensitivity:**
  - Never publish victim names, reporter identity, or narratives.
  - Fatal reports are always human-reviewed (DECIDED) and published as structured fields only, at least 14 days after the event.
- **Takedowns:** public form; human decision within 7 days; content hidden during review; outcomes in `audit_log`.
- **Obligations:** no mandatory reporting duty assumed; a runbook covers escalating apparent emergencies or crimes and legal requests.
- **Legal review (DECIDED):** not a launch gate. The owner handles legal review separately. Questions to raise:
  1. ToS and privacy policy wording, including the "not an emergency channel" and no-warranty/disclaimer language.
  2. Whether a 15+ age floor needs parental-consent handling in any US state targeted.
  3. Enforceability of the CC0-on-facts grant and internal-only narrative license in a clickwrap.
  4. Liability exposure from publishing incident facts and risk scores, including defamation risk for facts about named routes or guides.
  5. Takedown and legal-request handling (DMCA agent registration, subpoenas).
  6. MP data: displaying ice/mixed route facts, keeping the ice/mixed tick aggregates, and internal modeling use of other scraped MP data (Phase 2 legal Q1–Q3), and the Jev DPA terms.
  7. GDPR applicability if EU users are ever targeted.

## F. Experience personalization (later; Phase 3 v3)

**Start only when both hold:**

- At least 300 accepted incidents include the experience of those involved.
- Tick exposure joined to profile experience covers at least 30% of E in the relevant route types. Without that denominator, rates cannot be compared.

**Experience sources:** self-reported profile experience (A), the optional experience field on reports (C1), and the structured experience fact Phase 2a extracts from accident records where stated. Name matching and tick-based inference are declined (see Non-goals).

**Method:** a post-hoc multiplier on RR × CM, from a hierarchical Poisson with experience-level offsets per route type. It is shown only when the 90% CI excludes 1. The UX rules still apply (never "safe").

## G. Data model, API, screens

**Tables:** created via Alembic under `migrator`. The `triage_worker` role is created via SQL per the Phase 1 procedure.

- `users(id uuid, email citext unique, email_verified_at, password_hash, role, status, age_attested_at, tos_version, created_at, last_login_at)`
- `sessions(token_sha256, user_id, last_seen_at, expires_at)`
- `email_tokens(token_sha256, user_id, purpose, expires_at, used_at)`
- `profiles(user_id, display_name, certifications, home_area_id)`
- `profile_disciplines(user_id, discipline, level, max_grade, years_bucket)`
- `ticks` (see B)
- `incident_reports` (form fields, `narrative_redacted`, `status`, `routing`, `abuse_signals`, `license_version`, `ip_hmac`)
- `incident_report_raw(report_id, narrative_raw, purge_after)`
- `report_triage_results(id, report_id, triager, model_id, request_sha256, response_sha256, request, answers, calibrated, routing, latency_ms, input_tokens)`
- `review_actions(report_id, reviewer_id, action, before, after, reason)`
- `reviewed_incidents(report_id, status staged|vetoed|promoted, verification, veto_deadline, accident_id)`
- `audit_log(actor_type, actor_id, action, target_type, target_id, metadata, created_at)`. The `app` role gets INSERT and SELECT only, which makes the log append-only.
- `app_settings`
- `rate_limits`

**Endpoints** (`/api/v1`, all with typed Pydantic models):

- **Auth:** `POST /auth/{register,verify-email,login,logout,password-reset/request,password-reset/confirm}`.
- **Account:** `GET /me`, `PATCH /me/profile`, `GET /me/export`, `DELETE /me`.
- **Ticks:** `GET|POST /me/ticks`, `PATCH|DELETE /me/ticks/{id}`.
- **Reports:** `POST /incident-reports`, `GET /me/incident-reports[/{id}]`, `POST /me/incident-reports/{id}/appeal`.
- **Reviewer:** `GET /review/queue`; `GET /review/reports/{id}` (raw-text views logged); `POST /review/reports/{id}/actions` (approve, reject, edit, merge, escalate); `POST /review/staged/{id}/veto`.
- **Admin:** `GET|PUT /admin/triage-mode`, `GET /admin/triage/metrics`.
- **Public:** `POST /takedown-requests`.

**Screens (TypeScript):** auth (sign-up, sign-in, verify, reset); profile and experience; privacy (export, delete); log-climb dialog and My ticks; report wizard (where → when → what → narrative → consent); My reports; reviewer queue and detail; admin triage dashboard.

## H. Testing, rollout, milestones, cost

**Tests (`uv run pytest`):**

- **Authz:** every route exercised as anonymous, owner, other user, reviewer, admin; IDOR tests on ticks and reports; a route-inventory test fails when a route lacks a declared auth dependency.
- **Security:** CSRF, session rotation, cookie flags, rate limits, argon2 parameters, single-use expiring reset tokens, a grep test for string-built SQL, redaction cases (including the route-name allowlist), and `triage_worker` privileges against real Postgres in CI.
- **Pipeline:** thresholds, the veto window, weight caps, the cluster rule, model-mismatch fallback, outage retry, and the kill switch. Jev is mocked in CI, and the eval harness runs manually.

**Rollout:** invite-only beta (at most 100 invite codes), then public after M4.6.

| M | Scope | Accept |
|---|---|---|
| 4.0 | Verify-before-build checklist, Resend `EmailSender`, ToS drafts, Cloudflare proxy setup (DNS to Cloudflare) | Answers in writing; origin reachable only via Cloudflare |
| 4.1 | Accounts, profiles, export/delete | Authz and security suites green; `security-auditor` review clean |
| 4.2 | Ticks and exposure aggregation | Phase 3 fixture fed; caps tested |
| 4.3 | Intake, redaction, `manual` queue | Report → human approve → `accidents` with provenance, end to end |
| 4.4 | Gold set, harness, `LocalTriager`, Jev in shadow mode | Eval committed; ≥4 weeks shadow with no routing effect |
| 4.5 | Auto-stage and auto-reject on | Eval gate met; override rate ≤5% for 2 weeks |
| 4.6 | Promotion, caps, retrain-trigger integration | Retrain consumes weighted rows; retrain guard tested |
| 4.7 | Public launch | ToS and privacy policy published; legal-questions list handed to owner; takedown runbook; rate limits and Cloudflare layer load-tested; alerts live |
| later | Personalization (F) | F conditions met |

**Cost per month (assumed 1,000 reports):** Jev ~2k tokens/report ≈ $0.08 (hard cap ≈ $2.10); Resend free tier; Turnstile and Cloudflare proxy free; ~200 MB extra worker RAM for spaCy/Presidio ≈ $2–5 on Railway (estimate). **Total under $10.**

## Owner decisions (recorded 2026-09-27, rev 3)

1. **Auth:** self-hosted sessions with argon2id. DECIDED.
2. **Email provider:** Resend (free tier) behind an `EmailSender` interface. DECIDED.
3. **Minimum age:** 15+, with minimized PII; GDPR consent ages are a future check if EU users are targeted. DECIDED.
4. **Cloudflare Turnstile:** yes. DECIDED.
5. **Submission license:** CC0 on facts, internal-only narrative license. DECIDED.
6. **Photos:** none in v1. DECIDED.
7. **Legal review:** not a launch gate; owner handles it separately using the questions list in E. DECIDED.
8. **Thresholds and weights:** 0.95 and 0.9 thresholds, 0.5 auto weight, 72 h veto, 25% cap, retuned after the M4.4 eval. DECIDED.
9. **What Jev sees:** mostly-redacted text plus catalog candidate lists (displayable fact fields only, never MP prose or internal data). DECIDED.
10. **Fatal reports:** always human-reviewed, published no sooner than 14 days after the event. DECIDED.
11. **Rate limiting and DDoS:** app-level limits, origin hardening and Turnstile, plus the Cloudflare free proxy in front of `safeascent.us` (DNS to Cloudflare, Porkbun stays registrar). DECIDED.
12. **Experience by name matching:** declined; self-reported experience only. DECIDED.
13. **Route catalog:** OpenBeta for rock, MP route facts for ice/mixed, Objective layer for mountaineering; all other MP data internal (Phase 3 M11). DECIDED.
