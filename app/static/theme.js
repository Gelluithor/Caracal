// The colour scheme before the first paint: light, dark, or the system's (the same choice as in the app).
try {
  const t = localStorage.getItem('caracal-theme');
  document.documentElement.dataset.theme = t === 'light' || t === 'dark' ? t : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
} catch (e) { /* storage blocked: the light scheme */ }
