---
id: source@reflections-memos-2026-02-19-memo-google-ads
title: "I Spent $4.2k on Google Ads for an AI Inbox Assistant. Here\u2019s What I\
  \ Actually Learned."
note-type: memo
date: '2026-02-19'
source-path: data/source/re/source@reflections-memos-2026-02-19-memo-google-ads/index.md
source-category: reflections/memos
source-type: note
citation-key: reflections-memos-2026-02-19-memo-google-ads
---

# I Spent $4.2k on Google Ads for an AI Inbox Assistant. Here’s What I Actually Learned.

This is the **Google Ads-only** retro for Inbot. Product postmortem is separate; this one’s just: what happened on Google, what worked, what didn’t, and what I’d repeat.

---

## What I Set Out to Do

Goal: **use Google Search as a fast truth serum**, not build a perfectly efficient CAC engine.

- Budget philosophy: **“Rule of 100”** flavor – roughly **$100/day**, willingness to burn a few thousand to get directional answers.
- Objective: find out **who responds**, what they think they’re buying, and how far they’ll walk down a funnel *before* chasing LTV or scale.

---

## Three Phases of the Funnel

### 1) Waitlist phase (ads → waitlist)

- Spend: first **2–3 weeks** at **$100/day** to a pure waitlist.
- Result: ~**150 waitlist signups**, roughly **1%** from click → signup.
- Observations:
  - Mixed audience: solo pros + big orgs (including state Departments of Education).
  - Waitlist signups felt encouraging but were **weak evidence**: no pricing, no real commitment, no behavior after the initial opt-in.

**Lesson:** Waitlist is a *lead magnet*, not validation. Treat it as “now I have people to talk to,” not “the market has spoken.”

---

### 2) Direct SaaS funnel (ads → connect Gmail → pay)

- Flow: click ad → connect Gmail → grant full permissions → pay (even with a free trial).
- Result: **activation happened**, but **payments were basically zero**.
- Root friction:
  - **Trust**, not price. Asking a cold Google user to hand over their inbox and card details to a new brand was too big a jump.

**Lesson:** On Google, “free trial” is not a magic bullet. If the *real* objection is sensitive access, changing price mechanics doesn’t fix it.

---

### 3) Lead magnet funnel (ads → free Inbox Health Report → trial)

- New front door: **“Free Inbox Health Report”** as a lead magnet.
- Mechanics:
  - Big drop-offs *before* permissions and at the permissions modal.
  - But of those who **completed the report**, ~**90%+** went on to start a trial.
- Interpretation:
  - Delivering a **personalized diagnostic** first made the product credible.
  - Ads could reliably drive people to a value moment; the *economic* gap (willingness to pay, intensity of pain) showed up **after** that.

**Lesson:** For high-trust products, paid search works best when the ad sells a **diagnostic** that earns the right to ask for access, not the product itself.

---

## How I Ran the Ads (and What Mattered)

- **Measurement:** 
  - Built internal tracking early; treated “active session,” permission modal views, report starts, and trial starts as separate rungs.
  - Discovered that only **~50–70%** of Google’s reported clicks became “real sessions.”

- **Iteration cadence:**
  - Rough rule: **~50 meaningful sessions** before calling a test.
  - Exception: obvious anomalies (e.g., a **$180 click at 1am**) triggered immediate changes and tighter bid caps.

- **Bidding & control:**
  - Started broad with more algorithmic freedom.
  - Moved to **fixed bids and upper bounds per keyword** once I had baseline CPCs and saw risk of runaway bids.

**Meta-lesson:** once basics are sane (CPC range, tracking, funnel visibility), tinkering with bid knobs is low leverage compared to fixing what the ad is *promising* and what the landing experience actually delivers.

---

## Rules I’d Reuse Next Time (Ads-Specific)

- **Waitlist ≠ validation.** Use it to start conversations, not to declare victory.
- **Sell a diagnostic, not the whole product, on cold search** when trust/permissions are the main friction.
- **Define “meaningful click” yourself.** Google’s click count is an input; “active session” is the first real metric.
- **Instrument a full ladder.** Click → session → key micro-commitments → core value moment → money. Make sure every step is visible.
- **Use budget to buy answers, not cope.** A few thousand dollars was enough to show: “traffic exists, health report builds trust, but economics are weak.” That’s a win if you’re honest about what you learned.
