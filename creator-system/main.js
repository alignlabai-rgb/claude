// ===== Navigation =====
const hamburger = document.querySelector('.nav-hamburger');
const navLinks = document.querySelector('.nav-links');
if (hamburger) {
  hamburger.addEventListener('click', () => navLinks.classList.toggle('open'));
}

// Active nav link
const currentPage = location.pathname.split('/').pop() || 'index.html';
document.querySelectorAll('.nav-links a').forEach(a => {
  const href = a.getAttribute('href');
  if (href === currentPage || (currentPage === '' && href === 'index.html')) {
    a.classList.add('active');
  }
});

// ===== Scroll animations =====
const observer = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.classList.add('fade-up');
      observer.unobserve(e.target);
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll('.card, .stat-card, .pipeline-step').forEach(el => {
  observer.observe(el);
});

// ===== Interactive Pipeline Demo =====
const demoSteps = [
  {
    label: 'ネタ分析',
    title: '🔍 Step 1 — ネタ分析完了',
    rows: [
      ['ジャンル', '社会・労働トレンド'],
      ['ターゲット', 'Z世代 / 20代社会人'],
      ['推定エンゲージメント', '高（共感型コンテンツ）'],
      ['検出キーワード', 'Quiet Quitting, 燃え尽き症候群, SNS'],
      ['推奨フォーマット', 'ショート動画 60秒 + note記事'],
    ]
  },
  {
    label: 'スクリプト',
    title: '📝 Step 2 — スクリプト生成完了',
    rows: [
      ['Scene 1 (0-15s)', '「残業しない＝やる気なし？　その常識、Z世代が壊してます」'],
      ['Scene 2 (15-40s)', 'Quiet Quittingとは何か。燃え尽きへの防衛策として広がる背景を解説。'],
      ['Scene 3 (40-60s)', '「頑張りすぎない生き方」を選んだ若者の声とデータを紹介。'],
      ['ナレーション文字数', '約420文字（ElevenLabs: 約38秒）'],
      ['BGMトーン指定', 'Lo-fi / 落ち着いた / 共感系'],
    ]
  },
  {
    label: '映像生成',
    title: '🎬 Step 3 — 映像生成完了',
    rows: [
      ['画像生成', 'Midjourney v7 × 6枚（Scene別）'],
      ['動画クリップ', 'RunwayML Gen-4 × 3本 / 各16秒'],
      ['ナレーション音声', 'ElevenLabs v3 生成完了（39.2秒）'],
      ['BGM', 'Suno AI v4 生成完了（Lo-fi 60秒）'],
      ['合計API費用', '¥218（RunwayML ¥140 + その他 ¥78）'],
    ]
  },
  {
    label: '品質評価',
    title: '⭐ Step 4 — 品質評価完了',
    rows: [
      ['総合スコア', '91 / 100 ✓ 公開基準（85点）クリア'],
      ['視覚品質', '94点 — 画像クオリティ・編集テンポ良好'],
      ['エンゲージメント予測', '89点 — 共感ワード密度・ターゲット合致'],
      ['競合比較', '86点 — 同ジャンル上位20%相当'],
      ['判定', '✅ 自動公開フラグON'],
    ]
  },
  {
    label: '配信',
    title: '🚀 Step 5 — 配信完了',
    rows: [
      ['YouTube', '投稿完了 — 最適時間 20:00（平日）自動選択'],
      ['note', '記事版（2,400字）自動生成・投稿完了'],
      ['タグ最適化', '#QuietQuitting #Z世代 #働き方 を自動付与'],
      ['収益化設定', 'YouTube広告 ON / note有料マガジン追加'],
      ['完了通知', 'Signal に通知送信済み ✓'],
    ]
  }
];

let currentStep = -1;

function renderDemoStep(step) {
  const content = document.getElementById('demoOutputContent');
  const stepItems = document.querySelectorAll('.demo-step-item');
  const stepLines = document.querySelectorAll('.demo-step-line');

  stepItems.forEach((item, i) => {
    item.classList.remove('active', 'done');
    if (i < step) item.classList.add('done');
    else if (i === step) item.classList.add('active');
  });

  stepLines.forEach((line, i) => {
    line.classList.toggle('done', i < step);
  });

  if (step < 0) {
    content.innerHTML = '<div class="demo-output-placeholder">▶ 「次のステップ」を押してパイプラインを進める</div>';
    return;
  }

  const data = demoSteps[step];
  let html = `<div style="font-size:0.82rem; font-weight:700; color:var(--accent); margin-bottom:0.75rem;">${data.title}</div>`;
  html += data.rows.map(([k, v]) =>
    `<div class="demo-output-row"><span class="demo-output-key">${k}</span><span class="demo-output-val">${v}</span></div>`
  ).join('');
  content.innerHTML = html;
}

const nextBtn = document.getElementById('demoNext');
const resetBtn = document.getElementById('demoReset');

if (nextBtn) {
  nextBtn.addEventListener('click', () => {
    if (currentStep < demoSteps.length - 1) {
      currentStep++;
      renderDemoStep(currentStep);
      if (currentStep === demoSteps.length - 1) {
        nextBtn.textContent = '完了 ✓';
        nextBtn.disabled = true;
        nextBtn.style.opacity = '0.5';
      }
    }
  });
}

if (resetBtn) {
  resetBtn.addEventListener('click', () => {
    currentStep = -1;
    renderDemoStep(-1);
    if (nextBtn) {
      nextBtn.textContent = '次のステップへ →';
      nextBtn.disabled = false;
      nextBtn.style.opacity = '1';
    }
  });
}

// ===== KPI bar animation on scroll =====
const kpiObserver = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      const fills = e.target.querySelectorAll('.kpi-bar-fill');
      fills.forEach(fill => {
        const target = fill.style.width;
        fill.style.width = '0%';
        requestAnimationFrame(() => {
          setTimeout(() => { fill.style.width = target; }, 50);
        });
      });
      kpiObserver.unobserve(e.target);
    }
  });
}, { threshold: 0.2 });

const kpiSection = document.getElementById('kpi');
if (kpiSection) kpiObserver.observe(kpiSection);
