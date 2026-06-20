document.addEventListener('DOMContentLoaded', () => {
  const manual = document.body.dataset.manual || 'manual';
  const storageKey = `operation-progress:${manual}`;
  const views = [...document.querySelectorAll('.view')];
  const navButtons = [...document.querySelectorAll('.nav button')];
  const sidebar = document.querySelector('.sidebar');

  function showView(id) {
    views.forEach(view => view.classList.toggle('active', view.id === id));
    navButtons.forEach(button => button.classList.toggle('active', button.dataset.view === id));
    window.scrollTo({ top: 0, behavior: 'smooth' });
    sidebar?.classList.remove('open');
  }

  navButtons.forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
  document.querySelector('.mobile-toggle')?.addEventListener('click', () => sidebar?.classList.toggle('open'));

  document.querySelectorAll('pre').forEach(pre => {
    const wrap = document.createElement('div');
    wrap.className = 'code';
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);
    const button = document.createElement('button');
    button.className = 'copy';
    button.textContent = 'Copy';
    button.addEventListener('click', async () => {
      await navigator.clipboard.writeText(pre.innerText);
      button.textContent = 'Copied';
      setTimeout(() => { button.textContent = 'Copy'; }, 1200);
    });
    wrap.appendChild(button);
  });

  const checks = [...document.querySelectorAll('.check input[type="checkbox"]')];
  const saved = JSON.parse(localStorage.getItem(storageKey) || '{}');
  checks.forEach((check, index) => {
    check.checked = Boolean(saved[index]);
    check.addEventListener('change', () => {
      saved[index] = check.checked;
      localStorage.setItem(storageKey, JSON.stringify(saved));
      updateProgress();
    });
  });

  function updateProgress() {
    const done = checks.filter(check => check.checked).length;
    const pct = checks.length ? Math.round(done / checks.length * 100) : 0;
    const fill = document.querySelector('.progress-fill');
    const text = document.querySelector('.progress-value');
    if (fill) fill.style.width = `${pct}%`;
    if (text) text.textContent = `${pct}%`;
  }
  updateProgress();

  const search = document.querySelector('.search');
  search?.addEventListener('input', () => {
    const query = search.value.trim().toLowerCase();
    document.querySelectorAll('.card, .quick, .flow-step, tr').forEach(node => {
      node.classList.toggle('search-hidden', query && !node.textContent.toLowerCase().includes(query));
    });
    if (query) views.forEach(view => view.classList.add('active'));
    else showView(navButtons.find(button => button.classList.contains('active'))?.dataset.view || views[0]?.id);
  });
});
