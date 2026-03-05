(() => {
  const next = [...document.querySelectorAll('button')].find((el) =>
    ((el.textContent || '').trim()) === 'Next'
  );
  if (!next) return false;
  next.click();
  return true;
})();
