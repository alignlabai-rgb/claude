/**
 * Claude Code Operation Guide — Interactive Script
 * Handles navigation, animations, typing effects, filters, roadmap progress.
 */
document.addEventListener('DOMContentLoaded', () => {

  // ═══════════════════════════════════════
  //  1. Navigation — View Switching
  // ═══════════════════════════════════════
  const navTabs = document.querySelectorAll('.nav-tab');
  const viewPanels = document.querySelectorAll('.view-panel');

  navTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const targetView = tab.dataset.view;
      navTabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      viewPanels.forEach(panel => {
        panel.classList.remove('active');
        if (panel.id === `view-${targetView}`) {
          panel.classList.add('active');
          observeElements();
        }
      });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });

  // ═══════════════════════════════════════
  //  2. Hero Terminal — Typing Effect
  // ═══════════════════════════════════════
  const typingTarget = document.getElementById('typing-target');
  const typingCursor = document.getElementById('typing-cursor');
  const phrases = [
    'このプロジェクトの構造を説明して',
    'テストが全部通るまでバグを直して',
    'TypeScriptに移行して',
    'PRを作成して、タイトルも付けて',
    'CLAUDE.md を生成して',
    'セキュリティの脆弱性をチェックして',
    'このエラーログの原因を特定して修正して',
    'READMEとAPIドキュメントを生成して',
  ];
  let phraseIndex = 0, charIndex = 0, isDeleting = false, typingSpeed = 60;

  function typeLoop() {
    const currentPhrase = phrases[phraseIndex];
    if (!isDeleting) {
      typingTarget.textContent = currentPhrase.substring(0, charIndex + 1);
      charIndex++;
      if (charIndex === currentPhrase.length) {
        isDeleting = true;
        typingSpeed = 2000;
      } else {
        typingSpeed = 50 + Math.random() * 40;
      }
    } else {
      typingTarget.textContent = currentPhrase.substring(0, charIndex - 1);
      charIndex--;
      if (charIndex === 0) {
        isDeleting = false;
        phraseIndex = (phraseIndex + 1) % phrases.length;
        typingSpeed = 400;
      } else {
        typingSpeed = 25;
      }
    }
    setTimeout(typeLoop, typingSpeed);
  }
  setTimeout(typeLoop, 4000);
  setInterval(() => {
    if (typingCursor) typingCursor.style.opacity = typingCursor.style.opacity === '0' ? '1' : '0';
  }, 530);

  // ═══════════════════════════════════════
  //  3. Scroll Animations (Intersection Observer)
  // ═══════════════════════════════════════
  function observeElements() {
    const fadeElements = document.querySelectorAll('.fade-up:not(.visible)');
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry, index) => {
        if (entry.isIntersecting) {
          setTimeout(() => entry.target.classList.add('visible'), index * 80);
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });
    fadeElements.forEach(el => observer.observe(el));
  }
  observeElements();

  // ═══════════════════════════════════════
  //  4. Workflow Accordion
  // ═══════════════════════════════════════
  document.querySelectorAll('.workflow-card').forEach(card => {
    card.addEventListener('click', () => {
      const wfId = card.dataset.workflow;
      const detail = document.getElementById(`detail-${wfId}`);
      if (!detail) return;
      document.querySelectorAll('.workflow-detail.open').forEach(d => {
        if (d !== detail) d.classList.remove('open');
      });
      detail.classList.toggle('open');
      card.style.borderLeftColor = detail.classList.contains('open')
        ? 'var(--accent-warm)'
        : 'var(--accent-primary)';
    });
  });

  // ═══════════════════════════════════════
  //  5. Reading Progress Bar
  // ═══════════════════════════════════════
  const progressBar = document.getElementById('read-progress');
  function updateProgress() {
    const scrollTop = window.scrollY;
    const docHeight = document.documentElement.scrollHeight - window.innerHeight;
    const progress = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;
    progressBar.style.width = `${Math.min(progress, 100)}%`;
  }
  window.addEventListener('scroll', updateProgress, { passive: true });

  // ═══════════════════════════════════════
  //  6. Keyboard Navigation (1-6)
  // ═══════════════════════════════════════
  document.addEventListener('keydown', (e) => {
    const key = parseInt(e.key);
    if (key >= 1 && key <= 6 && !e.ctrlKey && !e.altKey && !e.metaKey) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      const tabs = Array.from(navTabs);
      if (tabs[key - 1]) tabs[key - 1].click();
    }
  });

  // ═══════════════════════════════════════
  //  7. Command Card Hover Micro-interactions
  // ═══════════════════════════════════════
  document.querySelectorAll('.command-card').forEach(card => {
    card.addEventListener('mouseenter', () => {
      const icon = card.querySelector('.cmd-icon');
      if (icon) { icon.style.transform = 'scale(1.1) rotate(-3deg)'; icon.style.transition = 'transform 0.3s ease'; }
    });
    card.addEventListener('mouseleave', () => {
      const icon = card.querySelector('.cmd-icon');
      if (icon) icon.style.transform = 'scale(1) rotate(0deg)';
    });
  });

  // ═══════════════════════════════════════
  //  8. QuickRef Card Click-to-Copy
  // ═══════════════════════════════════════
  document.querySelectorAll('.quickref-card').forEach(card => {
    const answer = card.querySelector('.quickref-answer');
    if (!answer) return;
    card.style.cursor = 'pointer';
    card.addEventListener('click', () => {
      const text = answer.textContent.trim();
      navigator.clipboard.writeText(text).then(() => {
        const origBg = answer.style.background;
        const origText = answer.textContent;
        answer.style.background = 'rgba(52, 211, 153, 0.2)';
        answer.style.transition = 'background 0.3s ease';
        answer.textContent = '✓ コピーしました';
        setTimeout(() => { answer.style.background = origBg || ''; answer.textContent = origText; }, 1200);
      }).catch(() => {
        const range = document.createRange();
        range.selectNodeContents(answer);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      });
    });
  });

  // ═══════════════════════════════════════
  //  9. Ambient Orb Parallax on Scroll
  // ═══════════════════════════════════════
  const orbs = document.querySelectorAll('.ambient-orb');
  function parallaxOrbs() {
    const scrollY = window.scrollY;
    orbs.forEach((orb, i) => {
      const speed = 0.03 + (i * 0.015);
      orb.style.transform = `translateY(${scrollY * speed}px)`;
    });
  }
  window.addEventListener('scroll', parallaxOrbs, { passive: true });

  // ═══════════════════════════════════════
  //  10. Command Filter Buttons
  // ═══════════════════════════════════════
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const filter = btn.dataset.filter;
      const parent = btn.closest('.view-panel');
      // Toggle active
      parent.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      // Filter cards
      const cards = parent.querySelectorAll('.command-card, .workflow-card');
      cards.forEach(card => {
        if (filter === 'all') {
          card.classList.remove('hidden-card');
        } else {
          const cat = card.dataset.category || card.dataset.level || '';
          card.classList.toggle('hidden-card', cat !== filter);
        }
      });
    });
  });

  // ═══════════════════════════════════════
  //  11. Roadmap Progress & Milestone Checkboxes
  // ═══════════════════════════════════════
  const STORAGE_KEY = 'cc_guide_milestones';

  function loadMilestones() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return saved ? JSON.parse(saved) : {};
    } catch { return {}; }
  }

  function saveMilestones(data) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch {}
  }

  function updateRoadmapProgress() {
    const checks = document.querySelectorAll('.milestone-check');
    const total = checks.length;
    const checked = document.querySelectorAll('.milestone-check.checked').length;
    const pct = total > 0 ? Math.round((checked / total) * 100) : 0;

    const fill = document.getElementById('progress-fill');
    const pctText = document.getElementById('progress-pct');
    const rankText = document.getElementById('progress-rank-text');

    if (fill) fill.style.width = `${pct}%`;
    if (pctText) pctText.textContent = `${pct}%`;

    if (rankText) {
      if (pct >= 100) rankText.innerHTML = 'Rank: <span>★ Master</span>';
      else if (pct >= 75) rankText.innerHTML = 'Rank: <span>Advanced</span>';
      else if (pct >= 40) rankText.innerHTML = 'Rank: <span>Intermediate</span>';
      else rankText.innerHTML = 'Rank: <span>Beginner</span>';
    }
  }

  // Initialize milestones
  const savedMilestones = loadMilestones();
  document.querySelectorAll('.milestone-check').forEach(check => {
    const id = check.dataset.milestone;
    if (savedMilestones[id]) {
      check.classList.add('checked');
      check.textContent = '✓';
    }
    check.addEventListener('click', (e) => {
      e.stopPropagation();
      const isChecked = check.classList.toggle('checked');
      check.textContent = isChecked ? '✓' : '';
      const data = loadMilestones();
      if (isChecked) data[id] = true;
      else delete data[id];
      saveMilestones(data);
      updateRoadmapProgress();
    });
  });
  updateRoadmapProgress();

  // Concept step stagger
  document.querySelectorAll('.concept-step').forEach((step, i) => {
    step.style.transitionDelay = `${i * 0.1}s`;
  });

});
