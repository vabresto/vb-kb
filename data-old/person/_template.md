---
template: true
person: <Full Name>
alias:
firm:
role:
location:
created-at: YYYY-MM-DD
updated-at: YYYY-MM-DD
website:
email:
linkedin:
relationship-status: cold
how-we-met:
last-contacted-at:
follow-up-due:
next-step:
intro-paths: []
focus-areas: []
looking-for: []
# looking-for:
# - ask:
#   details:
#   first-asked-at: YYYY-MM-DD
#   last-checked-at: YYYY-MM-DD
#   status: open
#   notes:
---

# <Full Name>

First mention of each known person or organization in page body text should be linked to its local KB page.

## Snapshot

- Why they matter:
- Current focus:
- Best way to engage:

## Employment History

Use frontmatter `firm` and `role` for the current (or most recent) role only.
Link organization names to org pages (for example, `[Org Name](../org/org-slug.md)`).

| Period | Organization | Role | Notes | Source |
| --- | --- | --- | --- | --- |
| Current | [<Organization>](../org/<org-slug>.md) | <Role> | <Short context> | [^profile-source] |
| YYYY-YYYY | [<Prior organization>](../org/<org-slug>.md) | <Role> | <Short context> | <source or Internal note> |

## Looking For

Track active asks with frontmatter `looking-for`; leave `looking-for: []` when there is no active ask.

| Ask | Details | First Asked | Last Checked | Status |
| --- | --- | --- | --- | --- |
| <Short ask> | <What they want and context> | YYYY-MM-DD | YYYY-MM-DD | open |

## Bio

![Headshot of <Full Name>](./images/<slug>.<ext>)[^profile-source]

<2-4 sentence sourced summary for quick coffee chat context.>[^profile-source]

## Conversation Notes

- [YYYY-MM-DD] <What you discussed, what they care about, and any promised follow-up.>

## Changelog

- [YYYY-MM-DD]: Created page from template

[^profile-source]: Source: [Profile or org page title](https://example.com). Verified/accessed on YYYY-MM-DD.
