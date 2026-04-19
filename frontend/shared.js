(function () {
  const themeButtons = Array.from(document.querySelectorAll('.theme-option'));
  const themeColorMeta = document.querySelector('meta[name="theme-color"]');
  const footerVersion = document.getElementById('footer-version');
  const THEME_KEY = 'agents-skill-eval-theme';

  function applyTheme(theme, persist = true) {
    const resolvedTheme = theme === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = resolvedTheme;
    if (themeColorMeta) {
      themeColorMeta.setAttribute('content', resolvedTheme === 'light' ? '#f3f4f6' : '#0a0a0a');
    }
    themeButtons.forEach((button) => {
      const isActive = button.dataset.themeValue === resolvedTheme;
      button.setAttribute('aria-pressed', String(isActive));
    });
    if (!persist) {
      return;
    }
    try {
      localStorage.setItem(THEME_KEY, resolvedTheme);
    } catch (error) {
      // Ignore storage access errors.
    }
  }

  themeButtons.forEach((button) => {
    button.addEventListener('click', () => applyTheme(button.dataset.themeValue));
  });

  async function loadVersion() {
    if (!footerVersion) {
      return;
    }
    try {
      const response = await fetch('/version', { cache: 'no-store' });
      if (!response.ok) {
        throw new Error('version unavailable');
      }
      const payload = await response.json();
      const commit = String(payload.commit || payload.version || '').trim();
      footerVersion.textContent = commit ? `Deployed version: ${commit.slice(0, 7)}` : 'Deployed version: unavailable';
    } catch (error) {
      footerVersion.textContent = 'Deployed version: unavailable';
    }
  }

  applyTheme(document.documentElement.dataset.theme, false);
  loadVersion();
})();
