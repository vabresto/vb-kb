---
id: source@reflections-long-form-2026-02-19-post-mortem-google-ads
title: "Google Ads retrospective: inbox assistant lead-gen (waitlist \u2192 product\
  \ funnel \u2192 lead magnet)"
note-type: reflection
date: '2026-02-19'
source-path: data/source/re/source@reflections-long-form-2026-02-19-post-mortem-google-ads/index.md
source-category: reflections/long-form
source-type: note
citation-key: reflections-long-form-2026-02-19-post-mortem-google-ads
---

## Google Ads retrospective: inbox assistant lead-gen (waitlist → product funnel → lead magnet)

### 0) Scoreboard (anchor the learnings)

- **Spend:** $4.2k
- **Impressions:** 17.2k
- **Clicks (Google):** 717 → **~$5.86 CPC**
- **My “real” top-of-funnel:** 275 active sessions → **~38%** of clicks
- Funnel:
    - 68 permissions modal
    - 47 user created
    - 33 inbox connected
    - 32 report started
    - 26 upsell/continuation viewed
    - 24 free trials started
    - **Paid:** 0
- Users:
    - 18 peak active (12 at sunset notice; 10 still active now)
    - **Sunset replies/objections:** 0

### 1) Business context and intent

**Product:** a system for helping users with their email inbox (initially positioned as a productivity/time-saver).

**Campaign intent (all lead-type):**

- **Waitlist signups** (early validation).
- **SaaS onboarding flow** (connect inbox → progress through product → pay).
- **Best-performing direction (late):** **“Free inbox health report” lead magnet** → free trial (lead magnet as the front door).

**What you were optimizing for conceptually:** “Find real demand quickly,” not “perfect CAC/LTV.” You wanted directional market feedback with enough measurement to avoid flying blind.

---

### 2) Initial setup and operating constraints

**Budget philosophy:** Alex Hormozi “Rule of 100”: **$100/day for 100 days** (commitment mindset), but you **did not run it blindly**—you were willing to pivot earlier when signals were strong.

**First campaign:** pure waitlist at **$100/day**.
**Target hypothesis:** business execs/founders/time-poor professionals.
**Runtime / volume (waitlist phase):**
- ~**2–3 weeks**
- ~**150 waitlist signups**
- Funnel conversion (your site-side measurement):
    - ~**10%** from ad click → landing page action (“lineup stage”)
    - ~**10%** from landing page → waitlist signup
    - (Net: ~1% from click to waitlist, based on your described steps)

**Notable signup composition observations:**

- Mix of personal emails (large share), plus some org emails.
- Some surprisingly “big org” signups (state Departments of Education, large education company).
- Some foreign-domain signups despite US targeting (Arabic-language sites, etc.).
- You discovered that many “custom domains” were effectively backed by Gmail.

---

### 3) Measurement and instrumentation (core strength of your approach)

You treated measurement as foundational:
- You built **internal funnel tracking** early (analytics background).
- You tracked sessions using **first_seen / last_seen timestamps**, and **filtered out** trivial bounces (e.g., first_seen == last_seen).
- You aligned internal funnel events with Google Ads reporting via **server-side event exports**:
    - Daily **CSV uploads** to Google including funnel events.
    - Events configured mostly as **secondary conversions**, used to keep Google “in sync” with the funnel you actually cared about.

**Key philosophy:** do not rely exclusively on client-side tracking due to blockers/inconsistency; server-side tracking is more reliable but adds complexity—worth it if you expect ongoing ad spend / multiple campaigns.

---

### 4) Earliest wrong assumptions / mistakes

#### Mistake 1: Treating waitlist signups as “validation”

You learned that “people signed up” is weak evidence.

- Signups did not prove:
    - the problem was correctly understood,
    - the product matched what they thought they were getting,
    - willingness to pay existed,
    - the value proposition was compelling at your intended pricing.

**Primary correction:** you needed to **talk to users**; waitlist alone wasn’t enough.

#### Mistake 2: Early user avatar mismatch (and not responding fast enough)

You started with an exec/founder hypothesis but saw unexpected segments (education/enterprise-ish).  
You explicitly chose to **ignore enterprise leads** because:
- you couldn’t support enterprise quality requirements,
- sales cycles felt too long,
- you wanted B2C proof first.

That was a rational constraint-based decision, but it also meant you might have ignored the most “obvious” monetizable segment at the time.

#### Mistake 3: Asking for too much trust too soon (funnel friction)

Your early SaaS flow effectively asked users to:
1. click ad
2. connect Gmail
3. grant full read/write permissions
4. pay (or even with free trial enabled)

Result: **near-total drop-off at payments**, even after enabling a 14-day free trial. The trust ask was not justified by the user’s prior experience with you.

---

### 5) What produced step-function improvements

#### The big pivot: “Free inbox health report” as lead magnet

This reframed the offer from “trust us with your inbox and pay” to “get immediate value first.”

What you observed:
- Still meaningful drop-offs early:
    - ~70% drop between landing page arrival → CTA click.
    - Additional ~40–50% drop when CTA opened a modal explaining subscription conditions.
- But once users **connected and saw the report**, conversion jumped:
    - **~90%+ activation** from “health report generated” → **free trial enabled**.

**Interpretation:**  
The product became “credible” only after delivering concrete, personalized value. Your earlier funnel tried to extract commitment before earning trust.

---

### 6) Iteration cadence: what you changed, when, and why

Your iteration philosophy:
- Let experiments run until you have **non-trivial sample size**.
- Your rough rule: **~50 meaningful top-of-funnel clicks** before drawing conclusions.

But “meaningful click” was not the same as what Google reported:

- You estimated only **~50–70%** of Google-reported clicks became “active sessions” by your filters.

You also believed day-of-week mattered:
- You suspected **Wednesday** performed best (problem has “aged” a few days; users feel pain).
- Weekends produced volume but possibly lower quality.
- Mondays/Fridays felt weaker.

**When you changed things fast:**  
One “hard stop” event forced immediate action: a **$180 click at ~1am**, exceeding daily budget expectations. That triggered tighter control.

---

### 7) Bidding / campaign control lessons

You started broader, then moved toward control:
- Initial: general search campaign with more algorithmic freedom.
- After the expensive outlier + general need for control:
    - moved to **fixed-cost bidding** / tighter caps,
    - set **upper bounds** per keyword,
    - toggled settings to prevent Google from “stretching” bids based on its own predicted fit.

**Rule of thumb emerging:**  
Start with enough breadth to learn, but **constrain the system quickly** once you have baseline CPC and early signals.

---

### 8) What “should have worked” but didn’t

The intuitive play that failed:
- “Offer the product directly with a free trial; that should reduce friction.”

But it didn’t, because:
- The real friction wasn’t price risk; it was **trust and perceived risk**:
    - granting inbox access,
    - believing you’ll deliver value,
    - fear of being charged or misused.

So “free trial” didn’t solve the underlying objection. The lead magnet did because it **proved value before asking for commitment**.

---

### 9) Traffic quality: what you think you learned (and what’s still unclear)

You have a conceptual understanding but not an internalized one yet:
- You suspect different sources/audiences produce “better-fit humans,” not just different numbers.
- You felt constrained by “Google gives what it gives,” and the vertical itself selects for certain CPC levels.

You also linked traffic quality to business fundamentals:
- If the business were more cash-flow positive / margin-rich, you could “buy” more learning and tolerate high CPC while you refine targeting.

**If unlimited budget:**  
You’d run many micro-campaigns across different traffic sources/segments to map quality differences and build intuition.

---

### 10) Concrete “rules of thumb” you articulated

**Offer design / funnel**
- Don’t ask for high trust up front unless you’ve earned it.
- Lead magnets work because they lower the commitment threshold and start a relationship.
- Waitlist is often itself a lead magnet; don’t overcomplicate too early.
- Keep it simple until you have a reason not to.

**Message alignment**
- Best results came when **ad hook + ad body + landing page promise were tightly aligned**.
- The landing page must deliver exactly what the click was “purchased” for.

**Campaign type**

- Prefer **Search (high intent)** early.
- Avoid broad/opaque systems (e.g., Performance Max) until later, when you have stronger conversion signals and can afford exploration.

**Measurement**
- Instrument funnel stages early.
- Consider server-side tracking if ads are going to be a persistent motion.
- Build a “ladder of intent” in conversions:
    - track many funnel events from the start,
    - set an early-stage conversion as **primary** initially,
    - then promote deeper funnel events to primary later as volume allows (hand-holding Google toward better-fit users).

**Iteration**

- Give changes time to accrue signal (targeting ~50 meaningful top-of-funnel events).
- Break that rule only for “obvious emergencies” (e.g., runaway CPC/spend anomalies).

---

### 11) The biggest meta-lesson (process, not tactics)

You can’t reason your way to truth without reality contact:
- Interface with the market early.
- Signups are not understanding.
- “Finger on the pulse” matters: why did they click, why did they sign up, what did they expect?

Also: if you solve the problem definition at the waitlist stage, you gain:
- correct terminology,
- sharper messaging,
- clearer value proposition for the people you’re trying to serve.

---

### 12) Practical additions for “next time” (based on your own stated gaps)

#### A) Operationalize “talk to users” (lightweight)

You suggested a plausible scalable tactic:
- When someone signs up, send a **human-sounding follow-up email** after ~15 minutes:
    - not a calendar link immediately,
    - 1–2 short questions:
        - “What were you hoping this would help with?”
        - “What’s the biggest inbox pain right now?”
- Expect very low reply rate; the goal is to find early adopters.
- Handle responders manually.

#### B) Enterprise leads (if revisited)

You didn’t pursue them, but if you did:
- Use automation only to **start conversation**, then quickly switch to manual for the rare responders.
