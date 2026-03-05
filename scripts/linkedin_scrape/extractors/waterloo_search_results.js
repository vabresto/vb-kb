(() => {
  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
  const cleanUrl = (href = "") => {
    try {
      if (href.startsWith("/in/")) {
        return `${location.origin}${href.split("?")[0]}`;
      }
      const parsed = new URL(href, location.origin);
      return `${parsed.origin}${parsed.pathname}`;
    } catch {
      return href;
    }
  };

  const parseName = (text) => {
    const match = /View\s+(.+?)[’']s\s+profile/i.exec(text || "");
    return match ? clean(match[1]) : "";
  };

  const parseConnection = (text) => {
    const match = /(\d+)(?:st|nd|rd|th)\s*degree\s+connection/i.exec(text || "");
    if (!match) return "";
    const suffix = `${match[1]}`.toLowerCase();
    return `${match[0].replace(/(?:degree|connection|\s+)/gi, "").toLowerCase()}`;
  };

  const parseTitleOrg = (label) => {
    const raw = clean(label);
    let title = raw;
    let org = "";

    const atMatch = raw.match(/^(.+?)\s+@\s+(.+)$/);
    if (atMatch) {
      title = clean(atMatch[1]);
      org = clean(atMatch[2]);
      return { title, org };
    }

    const atWordMatch = raw.match(/^(.+)\s+at\s+(.+)$/i);
    if (atWordMatch) {
      title = clean(atWordMatch[1]);
      org = clean(atWordMatch[2]);
    }

    return { title, org };
  };

  const parseMutuals = (li, profileHref, profileName) => {
    const insight = li.querySelector(".entity-result__insights");
    const insightText = clean(insight?.textContent || "");

    const names = [...li.querySelectorAll("a[href*='/in/']")]
      .filter((a) => {
        const href = cleanUrl(a.href || "");
        const text = clean(a.textContent);
        if (!text || href === profileHref) return false;
        if (/^status is /i.test(text)) return false;
        if (text.toLowerCase() === profileName.toLowerCase()) return false;
        if (/View\s+.+[’']s\s+profile/i.test(text)) return false;
        return true;
      })
      .map((a) => clean(a.textContent));

    const namedMutuals = names.slice(0, 2).filter(Boolean).join("; ");
    const explicitTotalMatch = /(?:and\s+)?(\d+)\s+other\s+mutual\s+connections?/i.exec(insightText);
    const total = explicitTotalMatch ? Number(explicitTotalMatch[1]) : names.length;

    return { namedMutuals, total };
  };

  const rows = [];
  const seen = new Set();
  const profileAnchors = [...document.querySelectorAll("a[href*='/in/']")].filter((a) =>
    /View\s+.+[’']s\s+profile/i.test(clean(a.textContent || "")),
  );

  for (const anchor of profileAnchors) {
    const href = cleanUrl(anchor.href || "");
    if (!href || seen.has(href)) continue;
    seen.add(href);

    const li = anchor.closest("li");
    if (!li) continue;

    const name = parseName(anchor.textContent || "");
    const fullText = clean(li.textContent);
    const titleNode = [...li.querySelectorAll("div")].find((node) =>
      /(^|\s)t-14\s+t-black\s+t-normal/.test(node.className || "")
    );
    const { title, org } = parseTitleOrg(titleNode?.textContent || "");
    const { namedMutuals, total } = parseMutuals(li, href, name);

    rows.push({
      name,
      connection_degree: parseConnection(fullText),
      title,
      org,
      linkedin_url: href,
      named_mutuals: namedMutuals,
      mutual_total: String(total || 0),
    });
  }

  return rows;
})();
