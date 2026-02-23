---
id: note@reflections-memos-2026-02-19-memo-inbot
title: I Built an AI Inbox Assistant for 12 Months. It Went Down for Days. Nobody
  Complained.
note-type: memo
date: '2026-02-19'
source-path: data/note/re/note@reflections-memos-2026-02-19-memo-inbot/index.md
source-category: reflections/memos
---

# I Built an AI Inbox Assistant for 12 Months. It Went Down for Days. Nobody Complained.

I spent 12 months building **Inbot**, an **AI assistant for Gmail inbox management**, as my “one project for the year” experiment.

The most honest signal: **it had a two business-day outage and users didn’t complain.** That told me it wasn’t a *painkiller*—it was a *vitamin*.

## What I set out to do
A hard constraint for the year: **pick one project and stick with it** (to counter shiny-object syndrome). Mission accomplished on focus—but the market verdict was clear. My goal was to prove I could take one idea from zero → launch → paying users.

## The five moments that defined the outcome
1) **AAA → Inbot pivot**  
Started with “AI Automation for Agencies.” Early calls showed interest, but people wanted a *specific* automation outcome with clear dollars attached, not a vague platform promise. I pivoted to an inbox assistant.

2) **Google verification became the real bottleneck**  
Shipping an email-integrated product meant **security verification + restricted scope approval**, which delayed meaningful user feedback until the **second half of the year**.

3) **Stack rewrite cost a critical month**  
Engineering instability + “trust-sensitive UI needs to look legit” drove a major rewrite. It restored momentum, but burned a month when speed-to-feedback mattered most.

4) **The first funnel failed completely**  
**Ads → connect Gmail → pay** led to **100% drop-off at Stripe**. Even with a free trial, the ask was too big for a new brand with sensitive permissions.

5) **The “Health Report” worked for trust, not for economics**
Switching to **Ads → free Inbox Health Report → free trial** created a real “aha”:
- Big drop-offs *before* permissions and *at* the permissions modal  
- But **~90% of people who completed the report opted into the trial**

So: the product had value *after* people trusted it. The problem was getting them there at scale—and converting at the price needed.

## Root cause in one line
**Inbox cleanup was useful, but it was not solving a _massive_ pain.**  It priced like a **$3–$5/mo nice-to-have**, not a **$20/mo must-have**. The outage silence was the proof.

## What I’d do differently next time (the portable playbook)
- **Validate dollars, not vibes.** Get a paid pilot / paid concierge MVP / LOI with a real timeline *before* building.
- **Pick expensive pain, not mild annoyance.** Premium pricing requires measurable urgency (revenue, risk, compliance, downtime).
- **Earn trust before big asks.** For sensitive integrations: show value first (report/demo), then permissions, then payment.
- **Avoid “compliance-gated feedback loops” or at least validate cash _before_ you enter them.** If Google/Microsoft has to approve you before real users can pay you, start with a manual or low‑permissions version first (manual workflow / limited scope).
- **Use outage response as a truth metric.** If nobody yells when it breaks, you don’t have a business (or you don’t have enough activated users yet).

If you’re building an AI tool around email or calendars right now, I’m happy to share the full internal retro or jam on your problem selection in the comments. I’d rather you benefit from my sunk year than repeat it.
