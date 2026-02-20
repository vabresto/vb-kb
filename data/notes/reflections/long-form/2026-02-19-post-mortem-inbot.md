
# Project Retrospective Report (Jan 2025 → Feb 2026)

**Project:** Inbot (AI email inbox assistant)  
**Initial thesis:** “AI Automation for Agencies” (AAA) → evolved into a Gmail-focused inbox assistant 
**Date range covered:** ~Jan 2025 through ~Feb, 2026 (includes sunset notice)

---

## Table of Contents

1. Executive Summary
2. Original Intent, Constraints, and Success Criteria
3. Problem Evolution and Product Definition
4. Timeline (Month-by-Month)
5. Key Decisions and Inflection Points
6. Go-to-Market Experiments and Funnel Metrics
7. What Worked
8. What Didn’t Work
9. Root Causes (Why It Didn’t Become a Business)
10. Lessons Learned (Portable to the Next Project)
11. If Re-running This Year: Concrete “Do Differently” Playbook
12. Appendix: Metrics Snapshot + Open Questions to Capture

---

## 1. Executive Summary

Over ~12–13 months, the project set out to build a startup product under a strict constraint: **pick one thing and stick with it for the year** (to counter shiny object syndrome). The initial positioning—**AI automation for agencies (AAA)**—showed early interest in discovery calls, but the market feedback indicated confusion: prospects weren’t sure what the “offer” actually was and expected a specific automation pitched to them.

The effort converged on **Inbot**, an **AI assistant for Gmail inbox management**. The build was significantly slowed by the realities of shipping an email-integrated product, especially **Google security verification** and **restricted scope approval**, which delayed access to real user feedback until the **second half of the year**.

After launch, multiple marketing/onboarding approaches were tested. The initial funnel (ads → connect Gmail → pay) proved too trust-intensive and conversion collapsed at the payment step. A later funnel pivot (ads → free inbox “health report” → free trial) improved mid-funnel activation (high trial opt-in after report), but upstream trust and permission friction remained substantial, and paid conversion did not materialize at the needed price point (felt closer to a “$3/month nice-to-have” than “$20/month must-have”).

The decisive signal came when the service experienced extended downtime (~2 business days) and **users didn’t complain or churn loudly**—a strong indicator the product was not a “painkiller.” A sunset email was sent on **Feb 9, 2026**, and little to no feedback came back, reinforcing the conclusion: **some value existed, but not enough urgency/willingness-to-pay** for sustainable business traction.

---

## 2. Original Intent, Constraints, and Success Criteria

### Purpose of the year

- Build a startup-grade product end-to-end (idea → build → go-to-market → revenue attempt).
- Treat the year as both:
    - a business attempt, and
    - an intentional learning exercise (explore vs exploit).

### Hard constraint (non-negotiable)

- **One project only for the entire year.**
- Outcome: **Success** (stayed focused on the single project).

### Early assumption set

- Can build solutions; the bigger skill gap is **choosing problems people deeply care about** and **validating willingness-to-pay** early.
- I personally valued the inbox product, but learned I'm **not a representative customer**, and “useful” ≠ “I’d urgently pay for it.”

---

## 3. Problem Evolution and Product Definition

### Starting point: “AAA” — AI Automation for Agencies

- Discovery calls (~5–8; not quite 10).
- Feedback pattern:
    - Interest existed in “AI automation” generally,
    - but prospects expected a _specific_ workflow/automation offer rather than a broad platform pitch.

### Convergence: Inbot — AI assistant for your inbox (Gmail-first)

- Built a prototype rapidly (~1 week of focused effort).
- The product’s perceived value shifted during development into:
    - “an ad blocker for email” / inbox cleanup layer,
    - which is valuable, but primarily a **vitamin** vs a **painkiller**.

### Market fit tension observed

- Likely “best-fit” power users (execs, high-volume inboxes) often:
    - already have EAs/assistants, and/or
    - can’t connect accounts without enterprise security constraints.
- Some enterprise-ish interest surfaced (friend-of-friend at a large company; state education entities on waitlist), but:
    - the product felt too early to support enterprise procurement/security,
    - and outreach to those leads was limited.

---

## 4. Timeline (Month-by-Month)

> Dates are approximate.

### **January 2025 — Kickoff + discovery + prototype**

- Conducted ~5–8 agency discovery/sales calls (AAA framing).
- Pivot spark (exact trigger not recalled): moved toward inbox assistant concept.
- Built an early prototype in ~1 week using focused effort (vacation time / long weekend).

### **February 2025 — Contractor hiring and team formation**

- Context: agentic AI wasn’t strong enough yet to delegate reliably; outsourcing seemed like the best leverage; time was primary constraint.
- Spent most of February recruiting/interviewing contractors.
- Team reached ~3 contractors by late Feb.
- One contractor wasn’t a fit → replaced, extending into early/mid-March.

### **March–April 2025 — Building with a junior team**

- Reality: contractors were too junior (and at a junior price point); not “batteries included.”
- High management overhead:
    - style alignment,
    - code review,
    - monthly feedback loops,
    - ongoing guidance to reach expected quality/speed.

### **May 2025 — Team reduction**

- After multiple feedback cycles, overall output wasn’t scaling vs solo output.
- Consulted friends (consulting backgrounds) → decided it wasn’t worth it.
- Terminated 2 of 3 contractors; retained the highest performer (still required oversight).
- Takeaway crystallized: **management load negated the time-leverage thesis** (given team seniority + delegation skill gap).

### **May–June 2025 — Prototype maturation + Google compliance begins**

- Goal shifted to enabling real Gmail usage:
    - Start/prepare for **Google security audit / verification** and later **scope approval**.
- Process was messy/slow:
    - unclear requirements from Google + verification company,
    - patchy communication with verification company,
    - random blockers, slow execution.

### **Mid-June → Mid-July 2025 — Travel ~1 month**

- Project momentum/throughput on hold due to personal travel.

### **Late July 2025 — Security verification clears (approx.)**

- Security verification completed after returning.
- Next gate: **Google scope approval**, requiring a full end-to-end demo video with the exact permission flow.

### **Late July → August 2025 — Critical engineering blockers**

While trying to record the required demo flow, two major technical failures emerged:
- **Deadlocks** in Python FastAPI + async DB access (time sink; hard to reproduce/diagnose).
- **Segfault** in [Stripe](../../../org/stripe.md) client despite Python usage (another deep, destabilizing blocker).

### **August → Early September 2025 — Full stack rewrite**

- Already concerned the initial stack wouldn’t look professional enough:
    - Frontend/UI built with **NiceGUI**: functional but style-limited; risk of “unprofessional” look in a trust-sensitive context.
- Decision: rewrite basically everything except the IMAP interface.
- Duration: ~1 month.
- Outcome: working end-to-end system able to support recording the Google demo video.

### **September 2025 — Working product + waitlist acquisition via ads**

- With a stable end-to-end flow, started Google Ads to a waitlist.
- Metrics (approx.):
    - ~10% click-through rate (CTR)
    - ~10% waitlist signup rate
    - ~150 waitlist signups over ~2 weeks
- Notable: some “enterprise-ish” names appeared (state education-related entities; reps for large edu companies).

### **Mid-October 2025 — Launch**

- Launch timing: early/mid-October, roughly around Oct 10 (approx).
- Problem: time gap between waitlist acquisition and launch (~1 month), and follow-up was not strong enough.
- Result: weak conversion from waitlist to activation.

### **Oct–Nov 2025 — Initial paid funnel fails (trust ask too aggressive)**

- Initial flow: paid ads → onboarding → “Sign in with Google” → grant inbox access → pay via Stripe.
- Observed result:
    - **100% drop-off at Stripe checkout** (no conversions).
- Interpretation:
    - Asking for too much too soon:
        - new brand,
        - sensitive permissions (email),
        - immediate payment request.
    - No change even after introducing a 14 day free trial.

### **Late Nov → Early Dec 2025 — Funnel redesign: free “Inbox Health Report”**

- Pivoted onboarding to reduce perceived risk and increase immediate value:
    - Ads pitch: **free email inbox health report**
    - User connects inbox (read-only analysis of ~last 30 days)
    - Report results page → option to enable product via free trial

### **Dec 2025 — Improved activation for qualified users, but major top-of-funnel friction**

- Drop-offs observed:
    - ~60–70% drop before even reaching the permissions modal (landing → next action)
    - additional ~40–50% drop at the permissions modal
- Strong signal:
    - Among users who completed the health report, ~**90% opted into the free trial**
- Interpretation:
    - The “value proposition” resonated _after_ users saw personalized output,
    - but trust/permissions friction remained a massive gate.

### **Early–Mid January 2026 — Reliability signal: prolonged outage + silence**

- Attempted A/B tests to reduce the pre-modal drop; control still best.
- Around second week of January (approx), product broke for ~two business days.
- Users largely did not complain or escalate.
- This became a decisive signal: the product wasn’t critical enough.

### **Late Jan 2026 — Attempt to improve paid conversion**

- With trial funnel functioning, focus shifted to converting to paid.
- Pricing reality:
    - originally aimed around **$20/month**
    - emerging signal suggested perceived value closer to **$5/month**, if that
- Combined with low urgency signal from outage silence → decision leaned toward shutdown.

### **~Feb 10, 2026 — Sunset announcement**

- Sent email: sunsetting Inbot, request feedback, offer data export.
- Result: little/no replies → reinforced low attachment/low urgency.

---

## 5. Key Decisions and Inflection Points

### Decision: “One project for the year”

- **Benefit:** prevented thrash; forced full-cycle learning; delivered a complete arc (build → launch → market tests → closure).
- **Cost:** reduced ability to abandon a weak market early; time spent could not be reallocated to a higher-pain problem.

### Decision: Hire contractors early

- Hypothesis: my time is the bottleneck; delegation unlocks throughput.
- Reality:
    - team seniority mismatch + delegation skill gap created heavy management overhead,
    - output did not exceed solo output,
    - led to downsizing around May.

### Decision: Go Gmail-first (and accept Google audit overhead)

- Strategic simplification (scope down to Gmail only).
- But it introduced a **hard compliance gate** that delayed real customer feedback until late summer/early fall.

### Decision: Rewrite the product stack (Aug/Sept)

- Triggered by:
    - technical instability (deadlocks/segfaults blocking demo + launch),
    - UI/professionalism concerns (NiceGUI limitations).
- Outcome:
    - restored ability to proceed,
    - but consumed ~1 month during a critical period.

### Decision: Funnel pivot to “Health Report”

- Most effective GTM move in the project.
- It changed the ask from “trust us + pay us” to “get value first,” then convert.
- Strong mid-funnel metrics validated the idea _for the users who made it through trust gates_.

### Inflection: Outage + user silence

- The cleanest “truth serum” moment:
    - If a product is mission-critical, downtime triggers immediate complaints/escalations.
    - Silence indicated low dependency.

---

## 6. Go-to-Market Experiments and Funnel Metrics

### Campaign 1: Direct-to-product paid onboarding (Oct–Nov)

**Flow:** Ads → connect Gmail → pay  
**Result:** **100% drop at Stripe checkout**  
**Interpretation:** Trust barrier too high + value not demonstrated before paywall. 14 day free trial was not enough to move the needle.

### Campaign 2: Free “Inbox Health Report” (Late Nov/Dec onward)

**Flow:** Ads → landing page → permissions → health report → free trial opt-in  
**Observed behavior:**
- ~60–70% drop before the modal (landing friction / low intent / unclear value / general bounce)
- ~40–50% drop at modal (permissions fear)
- ~90% trial opt-in after completing report (strong “aha” once value is visible)

**Interpretation:**
- People who see personalized insights are highly likely to try the product.
- The main choke point is not “product usefulness,” it’s **trust + permissions + motivation** before the value moment.

### Pricing reality

- Target: ~$20/month
- Emergent signal: felt like ~$5/month value for many users (“vitamin” pricing).

---

## 7. What Worked

### 1) Focus discipline

- The one-project rule worked as intended.
- Shipped, marketed, iterated, and made an end decision—rare and valuable.

### 2) Rapid prototyping ability

- Early prototype in ~1 week shows strong build velocity when unblocked.

### 3) Funnel iteration produced a real insight

- “Health Report” was a meaningful discovery:
    - reduce trust ask,
    - deliver immediate, personalized value,
    - then present free trial.

### 4) Found a clean “product truth” metric

- Outage silence is an unusually honest signal about urgency and dependency.

### 5) Deep learning accumulation

Key skills strengthened:
- compliance realities for inbox products,
- delegation/contractor management lessons,
- GTM messaging and sequencing (value first vs pay first),
- the difference between “useful” and “must-pay.”

---

## 8. What Didn’t Work

### 1) Customer feedback came too late

- Months of building before meaningful external usage.
- Primary cause: Google verification + scope gating.
- Secondary cause: Didn't actively reach out to potential users soon enough nor frequently enough.

### 2) Initial positioning didn’t translate into a concrete offer

- “AI automation” interest existed, but buyers wanted a specific automation outcome, not a broad promise.

### 3) Team leverage didn’t materialize

- Junior contractors required continuous oversight.
- Management costs consumed the time I was trying to free.

### 4) Trust barrier was underestimated

- “Connect Gmail + grant permissions + pay” is an enormous ask for a new brand.
- Even with read-only framing, the perception is “access to my entire inbox.” This was exacerbated by re-using the read/write permissions we already had (read-only used a different API and would require a meaningful buildout + more security verifications).

### 5) Weak waitlist conversion follow-up

- Waitlist built, but launch lag + limited follow-up reduced conversion.

### 6) Value not urgent enough

- Product helped, but didn’t create strong dependency.
- Users did not react strongly to outages or to shutdown.

---

## 9. Root Causes

### Root Cause A: Insufficient pain / urgency (“vitamin vs painkiller”)

- Users benefited, but not enough to:
    - pay at target price,
    - complain when it broke,
    - fight to keep it alive when it shut down.

### Root Cause B: Feedback + revenue arrived too late

- Google audit/scope path delayed real-world validation.
- Without early LoIs / pre-sales / commitments, it was easy to build in a vacuum.

### Root Cause C: Trust and permissions are a structural GTM tax

- Inbox products carry unusually high adoption friction.
- This pushed us toward:
    - strong brand trust,
    - enterprise security narratives,
    - referrals,
    - or a very compelling “must-have” pain.

### Root Cause D: Market/customer mismatch

- The highest-need segment (execs) may:
    - already have assistants,
    - be locked behind IT/security.
- The segment willing to self-serve may not feel enough pain to pay.

### Root Cause E: Execution drag from complexity and rewrites

- Engineering instability and stack rewrite consumed time during the window where speed-to-feedback mattered most.

---

## 10. Lessons Learned

### Lesson 1: Validate willingness-to-pay early (not “interest”)

- “Yeah, I’d probably buy this” is weak.
- Must have:
    - pre-sale dollars,
    - LoIs with real timelines,
    - pilots with explicit success criteria,
    - or a manual service people pay for immediately.

### Lesson 2: Get users involved earlier than feels comfortable

- **The cost of building “the wrong thing” dwarfs the awkwardness of early prototypes.**

### Lesson 3: Trust sequencing matters

- For sensitive products, you must earn trust before big asks:
    - value demonstration,
    - transparency,
    - security posture,
    - social proof,
    - gradual permissioning.

### Lesson 4: Delegation requires either (a) senior talent or (b) strong training systems

- Junior teams can work, but need:
    - crisp specs,
    - tight review loops,
    - consistent standards,
    - and time allocated to leadership—otherwise leverage fails.

### Lesson 5: Keep waitlists warm

- “Progress updates / teasers / sizzle” aren’t fluff.
- They maintain engagement, allow feedback, and reduce the cold-start at launch.

### Lesson 6: Outage response is a brutally honest PM metric

- If nobody cares when it breaks, we’re not solving an urgent problem (or we have too few activated users).

---

## 11. If Re-doing This Year: A Concrete Playbook

### A. Week 1–2: Problem selection that forces truth

- Do 15–30 conversations (not 5–8) with a single ICP.
- Drive each call toward:
    - “What do you do today?”
    - “What does it cost (time/money/risk)?”
    - “What happens if it doesn’t get done?”
    - “Would you pay $X this month to make it go away?”

### B. By Week 3: Get money or commitment

Pick one:
- paid pilot,
- paid concierge MVP,
- LoI with procurement owner + timeline,
- or pre-sale with clear refund terms.

### C. Build the “smallest version that gets paid”

- If trust/permissions are heavy, start with:
    - manual workflow,
    - limited scope,
    - or “report first” model (what you discovered late, do early).

### D. If the product touches email/data/security

Design the adoption ladder intentionally:
1. anonymous value (examples, mock report, case study)
2. low-risk value (limited analysis, clear data handling)
3. optional deeper integration
4. only then: payment escalation / upsell

### E. Operationally: treat rewrites as a last resort

- If compliance gates require stability, prioritize:
    - boring reliability,
    - observability,
    - and “demo flow must never break.”

### F. If aiming premium pricing: choose premium pain

- Premium pricing needs premium urgency:
    - revenue, legal risk, compliance risk, downtime risk, churn risk, pipeline risk.
- Make the ROI obvious and measurable.

---

## 12. Appendix

### Metrics Snapshot (as stated)

- Discovery calls (Jan 2025): ~5–8
- Waitlist ads (Sept 2025-ish):
    - ~10% CTR
    - ~10% signup rate
    - ~150 signups over ~2 weeks
- Direct-to-pay funnel (Oct/Nov 2025): **100% drop at Stripe**
- Health report funnel (Dec 2025):
    - ~60–70% drop pre-modal
    - ~40–50% drop at modal
    - ~90% of report completers started free trial
- Reliability signal: ~2-day outage (Jan 2026) with minimal/no user complaints
- Sunset email: Feb 9, 2026; minimal/no replies
