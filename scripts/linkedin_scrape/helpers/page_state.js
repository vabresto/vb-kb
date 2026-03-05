(() => {
  const headingNode = [...document.querySelectorAll('h1, h2, [role="heading"]')].find((el) =>
    /Search results for waterloo alumni/i.test(el.textContent || "")
  );
  const headingText = headingNode ? (headingNode.textContent || '').trim() : '';
  const match = /Currently on the page\s+(\d+)\s+of\s+(\d+)\s+search result pages\./i.exec(headingText);
  const nextButton = [...document.querySelectorAll('button')].find((el) =>
    ((el.textContent || '').trim()) === 'Next'
  );

  return {
    page: match ? Number(match[1]) : 1,
    total: match ? Number(match[2]) : 1,
    heading: headingText,
    hasNext: !!nextButton,
  };
})();
