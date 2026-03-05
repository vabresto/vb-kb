(() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const normalize = (value) => clean(value).toLowerCase();
  const cleanUrl = (href = '') => {
    if (href.startsWith('/in/')) {
      return `${location.origin}${href.split('?')[0]}`;
    }
    try {
      const parsed = new URL(href);
      return `${parsed.origin}${parsed.pathname}`;
    } catch {
      return href;
    }
  };

  const parseProfileAnchor = (card) => {
    const anchors = [...card.querySelectorAll("a[href*='/in/']")].filter((a) => {
      const href = a.getAttribute('href') || '';
      return /\/in\//.test(href) && !/\/linkedin\/learning\//i.test(href) && !/\/pulse\//i.test(href);
    });

    if (!anchors.length) return null;
    return (
      anchors.find((a) => /View\s+.+[’'\u2018\u2019]\s*s\s+profile/i.test(a.textContent || '')) ||
      anchors.find((a) => {
        const label = normalize(a.textContent);
        if (!label) return false;
        if (/^status\s+is\b/.test(label)) return false;
        if (/^(connect|message|follow|ignore|accept|more|pending)\b/.test(label)) return false;
        return true;
      }) ||
      anchors[0]
    );
  };

  const parseNameFromAnchor = (anchor) => {
    const anchorText = clean(anchor.textContent || '');
    const visuallyHidden = clean(anchor.querySelector('.visually-hidden')?.textContent || '');
    const hiddenMatch = /View\s+(.+?)[’'\u2018\u2019]\s*s\s+profile/i.exec(visuallyHidden || anchorText);
    if (hiddenMatch) return clean(hiddenMatch[1]);

    const splitMatch = /^(.*?)View\s*/i.exec(anchorText);
    if (splitMatch) return clean(splitMatch[1]);
    return anchorText;
  };

  const parseTitleAndOrg = (card) => {
    const t14Lines = [...card.querySelectorAll('div')]
      .filter((el) => /\bt-14\b/.test(el.className || '') && /\bt-black\b/.test(el.className || '') && /\bt-normal\b/.test(el.className || ''))
      .map((el) => clean(el.textContent))
      .filter(Boolean);

    let title = t14Lines[0] || '';
    let org = '';

    if (title) {
      const explicitOrg = card.querySelector('.entity-result__primary-subtitle');
      if (explicitOrg) {
        org = clean(explicitOrg.textContent);
      } else if (t14Lines[1]) {
        org = t14Lines[1];
      } else if (/\bat\b/i.test(title)) {
        const bits = title.split(/\sat\s/i);
        if (bits.length > 1) {
          title = clean(bits[0]);
          org = clean(bits.slice(1).join(' at '));
        }
      }
      return { title, org };
    }

    const text = clean(card.textContent);
    let fallback = '';
    const marker = /(\d+)(?:st|nd|rd|th)\s*degree\s*connection/i;
    const matches = [...text.matchAll(marker)];
    if (matches.length >= 2) {
      const lastMatch = matches[matches.length - 1];
      fallback = clean(text.slice((lastMatch.index || 0) + lastMatch[0].length));
    } else {
      const match = marker.exec(text);
      if (match) {
        fallback = clean(text.slice((match.index || 0) + match[0].length));
      }
    }

    fallback = clean(
      fallback
        .replace(/\bis a mutual connection\b.*/i, '')
        .replace(/\band [0-9]+ others? are mutual connections?\b.*/i, '')
        .replace(/\bConnect$/i, '')
        .replace(/\bFollow$/i, '')
    );

    const atSplit = fallback.split(/\s+at\s+|\s*@\s*/i);
    if (atSplit.length >= 2) {
      title = clean(atSplit[0]);
      org = clean(atSplit.slice(1).join(' @ '));
    } else {
      title = fallback;
    }

    if (!org && title) {
      const locationMatch = /(.*)\s+(?:in|at)\s+([A-Z][\w\s]+,\s*[A-Z]{2}|[A-Za-z]+\s+Metropolitan\s+Area)$/i.exec(title);
      if (locationMatch) {
        title = clean(locationMatch[1]);
        org = clean(locationMatch[2]);
      }
    }

    return { title, org };
  };

  const connectionDegree = (card) => {
    const text = clean(card.textContent);
    const match = /(\d+)(?:st|nd|rd|th)\s*degree\s*connection/i.exec(text);
    if (!match) return '';
    return `${match[1]}${match[1] === '1' ? 'st' : match[1] === '2' ? 'nd' : match[1] === '3' ? 'rd' : 'th'}`;
  };

  const roleMatches = ({ title, org }) => {
    const text = `${title} ${org}`;
    const normalized = normalize(text);

    const isDirector = /\bvice?\s+president\b|\bvp\b|\bdirector\b|\bhead\b/i.test(text);
    const isOps = /\boperations?\b|\bops\b|\boperational\b/.test(normalized);
    const isClaims = /\bclaims?\b/.test(normalized);
    const isPolicy = /\bpolicy\b/.test(normalized);
    const isService = /\bservice\b/.test(normalized);
    const isAdministration = /\badministration\b/.test(normalized);
    const isTransformation = /\btransformation\b/.test(normalized);
    const isExcellence = /\bexcellence\b/.test(normalized);
    const isRegulatory = /\bregulatory\b/.test(normalized);
    const isReporting = /\breporting\b/.test(normalized);
    const isInsuranceContext = /(insurance|insurer|carrier|tpa|underwrit|agent|brokerage|broker|reinsur)/.test(normalized);

    const isOpsExcellence = isOps && (isExcellence || /operational excellence/.test(normalized));
    const isClaimsOps = isClaims && isOps;
    const isPolicyOrServiceOps = isOps && ((isPolicy && isAdministration) || (isPolicy && /admin/.test(normalized)) || isService);
    const isOpsTransformation = isOps && isTransformation;
    const isRegOrRepOps = isOps && (isRegulatory || isReporting);
    const isLeadershipOps = isDirector && isOps;

    return {
      isTarget: isLeadershipOps && isOps &&
        (isInsuranceContext || isClaims || isPolicy || isService || isRegulatory || isReporting || isTransformation || isExcellence),
      checks: {
        isClaimsOps,
        isPolicyOrServiceOps,
        isOpsExcellence,
        isOpsTransformation,
        isRegOrRepOps,
        isLeadershipOps,
        isInsuranceOps: isInsuranceContext || isClaims || isPolicy || isService || isTransformation || isRegulatory || isReporting || isExcellence,
      },
    };
  };

  const parseMutuals = (card, profileUrl, profileName) => {
    const insight = card.querySelector('.entity-result__insights');
    const candidateNames = insight
      ? [...insight.querySelectorAll("a[href*='/in/']")].map((a) => clean(a.textContent)).filter(Boolean)
      : [...card.querySelectorAll("a[href*='/in/']")].map((a) => clean(a.textContent)).filter(Boolean);

    const filtered = candidateNames
      .filter((name) => name && !name.toLowerCase().startsWith(profileName.toLowerCase()) && !/View\s+/.test(name))
      .filter((name) => !/connect|message|follow|status is/i.test(name.toLowerCase()));
    const uniqueNames = [...new Set(filtered)].filter((name) => normalize(name) !== normalize(profileName));
    const namedMutuals = uniqueNames.slice(0, 2).join('; ');

    let additional = 0;
    if (insight) {
      const match = /(?:and\s+)?(\d+)\s+other\s+mutual\s+connection/i.exec(clean(insight.textContent || ''));
      additional = match ? Number(match[1]) : 0;
    }

    return {
      named_mutuals,
      mutual_total: String((uniqueNames.length + additional) || 0),
    };
  };

  const cards = [...document.querySelectorAll('[data-view-name="search-entity-result-universal-template"]')];
  if (!cards.length) {
    const seen = new Set();
    const candidateCards = [...document.querySelectorAll('li')]
      .filter((li) => li.querySelectorAll("a[href*='/in/']").length > 0)
      .map((li) => {
        const profileAnchor = parseProfileAnchor(li);
        return profileAnchor ? { card: li, href: cleanUrl(profileAnchor.href || '') } : null;
      })
      .filter((item) => {
        if (!item || !item.href || seen.has(item.href)) return false;
        seen.add(item.href);
        return true;
      })
      .map((item) => item.card);

    return candidateCards
      .map((card) => {
        const profileAnchor = parseProfileAnchor(card);
        if (!profileAnchor) return null;
        const name = parseNameFromAnchor(profileAnchor);
        const profileUrl = cleanUrl(profileAnchor.href || '');
        const titleOrg = parseTitleAndOrg(card);
        const role = roleMatches(titleOrg);
        const mutual = parseMutuals(card, profileUrl, name);
        return {
          name,
          connection_degree: connectionDegree(card),
          title: titleOrg.title,
          org: titleOrg.org,
          linkedin_url: profileUrl,
          named_mutuals: mutual.named_mutuals,
          mutual_total: mutual.mutual_total,
          role_match: role.isTarget,
          role_flags: role.checks,
        };
      })
      .filter(Boolean);
  }

  return cards
    .map((card) => {
      const profileAnchor = parseProfileAnchor(card);
      if (!profileAnchor) return null;
      const name = parseNameFromAnchor(profileAnchor);
      if (!name) return null;

      const linkedinUrl = cleanUrl(profileAnchor.href || '');
      const titleOrg = parseTitleAndOrg(card);
      const role = roleMatches(titleOrg);
      const mutual = parseMutuals(card, linkedinUrl, name);

      return {
        name,
        connection_degree: connectionDegree(card),
        title: titleOrg.title,
        org: titleOrg.org,
        linkedin_url: linkedinUrl,
        named_mutuals: mutual.named_mutuals,
        mutual_total: mutual.mutual_total,
        role_match: role.isTarget,
        role_flags: role.checks,
      };
    })
    .filter(Boolean);
})();
