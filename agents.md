# Knowledge Base Rules

## Reference Integrity

- In structured reference fields (for example `known-people`), reference entities like people by file path link, not by plain name.
- Required format for `data/org/*.md` `known-people`: `"[Full Name](../person/person-slug.md)"`.
- If a referenced person file does not exist, create it before adding the reference.
- Rationale: names are not unique; file paths are unique and auditable.

### Example

```yaml
known-people:
  - "[David Tisch](../person/david-tisch.md)"
```

## Employment History

- In `data/person/*.md`, keep frontmatter `firm` and `role` for current (or most recent) employment only.
- Record prior roles in a dedicated `## Employment History` section.
- Use a table with columns: `Period`, `Organization`, `Role`, `Notes`, `Source`.
- Include source footnotes when public sources exist; use `Internal note` when details come from private context.
