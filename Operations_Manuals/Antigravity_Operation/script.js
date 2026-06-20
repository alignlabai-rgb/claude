document.addEventListener('DOMContentLoaded', () => {
    // --- Gamification State ---
    let xp = 0;
    const MAX_XP = 1000;
    const XP_PER_QUIZ = 100;
    const thresholds = { 1: 0, 2: 100, 3: 200, 4: 400, 5: 600, 6: 1000 };
    const rankNames = {
        1: { name: "Trainee", color: "var(--color-l1)" },
        2: { name: "Operator", color: "var(--color-l2)" },
        3: { name: "Automator", color: "var(--color-l3)" },
        4: { name: "Commander", color: "var(--color-l4)" },
        5: { name: "Architect", color: "var(--color-l5)" },
        6: { name: "MASTER", color: "var(--color-master)" }
    };

    const xpDisplay = document.getElementById('player-xp');
    const xpFill = document.getElementById('xp-bar-fill');
    const rankDisplay = document.getElementById('player-rank');
    const orb1 = document.querySelector('.orb-1');
    const masterBadge = document.getElementById('master-badge-container');

    function loadData() {
        const saved = localStorage.getItem('ag_mastery_save');
        if (saved) { xp = JSON.parse(saved).xp || 0; }
        updateUI(true);
    }

    function saveData() { localStorage.setItem('ag_mastery_save', JSON.stringify({ xp: xp })); }

    function updateUI(isLoad = false) {
        xpDisplay.innerText = xp;
        xpFill.style.width = `${Math.min((xp / MAX_XP) * 100, 100)}%`;

        let currentLevel = 1;
        for (let l = 6; l >= 1; l--) { if (xp >= thresholds[l]) { currentLevel = l; break; } }

        rankDisplay.innerText = rankNames[currentLevel].name;
        rankDisplay.style.color = rankNames[currentLevel].color;
        xpFill.style.background = rankNames[currentLevel].color;
        orb1.style.background = rankNames[currentLevel].color;

        for (let i = 2; i <= 5; i++) {
            const section = document.querySelector(`.level-card[data-level="${i}"]`);
            const statusIcon = section.querySelector('.status-icon');
            if (xp >= thresholds[i]) {
                if(section.classList.contains('locked') && !isLoad) shootConfetti(30);
                section.classList.remove('locked'); section.classList.add('unlocked'); statusIcon.innerText = "✅";
            } else {
                section.classList.add('locked'); section.classList.remove('unlocked'); statusIcon.innerText = "🔒";
            }
        }

        if (xp >= MAX_XP) {
            masterBadge.classList.remove('hidden');
            if(!isLoad) shootConfetti(100);
        } else { masterBadge.classList.add('hidden'); }

        document.querySelectorAll('.quiz-container').forEach(container => {
            const level = parseInt(container.getAttribute('data-quiz'));
            if (xp >= thresholds[level + 1]) {
                container.querySelectorAll('.quiz-btn').forEach(b => {
                    b.disabled = true; if(b.getAttribute('data-correct') === 'true') b.classList.add('correct');
                });
            }
        });

        if(typeof updateSkillTree === 'function') updateSkillTree(xp);
    }

    // --- Quiz Logic ---
    document.querySelectorAll('.quiz-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            if(this.disabled) return;
            const container = this.closest('.quiz-container');
            const isCorrect = this.getAttribute('data-correct') === 'true';

            container.querySelectorAll('.quiz-btn').forEach(b => b.classList.remove('correct', 'wrong'));

            if (isCorrect) {
                this.classList.add('correct');
                container.querySelectorAll('.quiz-btn').forEach(b => b.disabled = true);
                if (xp < MAX_XP) { xp = Math.min(xp + XP_PER_QUIZ, MAX_XP); saveData(); updateUI(); }
            } else {
                this.classList.add('wrong');
                this.style.transform = 'translateX(10px)'; setTimeout(() => this.style.transform = 'translateX(-10px)', 100);
                setTimeout(() => this.style.transform = 'translateX(0)', 200);
            }
        });
    });

    // --- View Toggles ---
    const toggleBtns = document.querySelectorAll('.toggle-btn');
    const views = document.querySelectorAll('.view-layer');
    toggleBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            toggleBtns.forEach(b => b.classList.remove('active')); btn.classList.add('active');
            views.forEach(v => v.classList.remove('active'));
            document.getElementById(btn.getAttribute('data-view')).classList.add('active');
        });
    });

    document.getElementById('reset-btn').addEventListener('click', () => {
        if(confirm('リセットして最初からやり直しますか？')) { xp = 0; saveData(); location.reload(); }
    });

    // --- Tools & Simulators Logic ---
    
    // 1. Prompt Sandbox
    document.getElementById('score-btn').addEventListener('click', () => {
        const text = document.getElementById('prompt-input').value;
        const feedback = document.getElementById('prompt-feedback');
        feedback.classList.remove('hidden');
        
        if(text.length < 10) {
            feedback.innerHTML = `<span style="color:#ef4444">Score: 10/100</span><br>短すぎます！AIには「背景」と「目的」を具体的に伝えると効果的です。`;
            return;
        }

        let score = 50;
        let comments = [];
        const hasGoal = text.match(/目的|要件|作って/);
        const hasConstraint = text.match(/条件|必ず|絶対|使わない/);
        const hasContext = text.match(/背景|現在|ユーザー/);

        if(hasGoal) { score += 20; comments.push("✅ 目的が明確です"); }
        if(hasConstraint) { score += 20; comments.push("✅ 制約条件が入っており素晴らしいです"); }
        if(hasContext) { score += 10; comments.push("✅ 背景情報があり、AIが文脈を理解しやすいです"); }

        let color = score > 80 ? '#34d399' : (score > 50 ? '#fbbf24' : '#ef4444');
        feedback.innerHTML = `<strong style="color:${color}; font-size:1.2rem;">Score: ${score}/100</strong><br>` + comments.join('<br>') + 
            (score < 100 ? `<br><br>💡 アドバイス: 「目的」「背景」「制約条件」の3つを意識して文章を組み立てると100点になります！` : `<br><br>🎉 完璧なプロンプトです！`);
    });

    // 2. Skill Maker
    document.getElementById('generate-skill-btn').addEventListener('click', () => {
        const name = document.getElementById('skill-name').value || 'my_custom_skill';
        const desc = document.getElementById('skill-desc').value || 'カスタムスキル';
        const rules = document.getElementById('skill-rules').value || '特になし';
        
        const outputBox = document.getElementById('skill-output');
        document.getElementById('out-name').innerText = name;
        
        const md = `---
name: ${name}
description: ${desc}
---

# Instructions
When this skill is invoked, you must strictly follow these rules:

${rules.split('\n').map(line => '- ' + line).join('\n')}
`;
        document.getElementById('skill-code').innerText = md;
        outputBox.classList.remove('hidden');
    });

    // 3. Swarm Visualizer (Updated)
    document.getElementById('simulate-swarm-btn').addEventListener('click', () => {
        const canvas = document.getElementById('swarm-canvas');
        const log = document.getElementById('swarm-log');
        const taskInput = document.getElementById('swarm-task-input').value || "新しいSNSアプリをフルスタックで開発して";
        
        // Clear old subagents
        canvas.querySelectorAll('.sub-agent').forEach(el => el.remove());
        log.innerHTML = `<p>[System] Task received: "${taskInput}"</p><p>[Planner] Analyzing task requirements and determining optimal agent swarm...</p>`;

        const colors = ['#34d399', '#fbbf24', '#f97316', '#a855f7'];
        const roles = ['Front', 'Back', 'DBA', 'QA'];
        const positions = [
            { x: -120, y: -80 },
            { x: -40, y: -140 },
            { x: 40, y: -140 },
            { x: 120, y: -80 }
        ];

        setTimeout(() => {
            log.innerHTML += `<p style="color: #fbbf24;">[Invoke] Spawning ${roles.length} subagents concurrently...</p>`;
            positions.forEach((pos, i) => {
                const sub = document.createElement('div');
                sub.classList.add('agent-node', 'sub-agent');
                sub.innerText = roles[i];
                sub.style.backgroundColor = colors[i];
                sub.style.color = '#000';
                sub.style.position = 'absolute';
                sub.style.bottom = '10px';
                sub.style.left = '50%';
                sub.style.marginLeft = '-15px'; // half of 30px width
                canvas.appendChild(sub);
                
                // Animate out
                setTimeout(() => {
                    sub.style.opacity = '1';
                    sub.style.transform = `translate(${pos.x}px, ${pos.y}px) scale(1)`;
                    log.innerHTML += `<p>[Agent ${roles[i]}] Online. Starting work...</p>`;
                    log.scrollTop = log.scrollHeight;
                }, 100 * (i + 1));
            });

            // Animate return
            setTimeout(() => {
                log.innerHTML += `<p>[System] All subagents reported success.</p><p style="color: #34d399;">[System] Task "${taskInput}" completed.</p>`;
                log.scrollTop = log.scrollHeight;
                canvas.querySelectorAll('.sub-agent').forEach((el, i) => {
                    setTimeout(() => {
                        el.style.transform = 'translate(0, 0) scale(0)';
                        el.style.opacity = '0';
                    }, 100 * i);
                });
                shootConfetti(30);
            }, 4500);

        }, 1500);
    });

    // 4. Terminal Simulator
    const termInput = document.getElementById('terminal-input');
    const termOutput = document.getElementById('terminal-output');
    if (termInput) {
        termInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                const val = this.value.trim();
                if (!val) return;
                
                termOutput.innerHTML += `<p class="term-input-line"><span class="term-prompt">user@ag:~$</span> ${val}</p>`;
                this.value = '';
                termOutput.scrollTop = termOutput.scrollHeight;
                
                setTimeout(() => {
                    if (val.startsWith('/goal')) {
                        termOutput.innerHTML += `<p class="term-output">[System] Goal mode activated. Entering autonomous loop.</p>`;
                        setTimeout(() => { termOutput.innerHTML += `<p class="term-agent-msg">-> Analyzing current state...</p>`; termOutput.scrollTop = termOutput.scrollHeight; }, 800);
                        setTimeout(() => { termOutput.innerHTML += `<p class="term-agent-msg">-> Executing 'grep_search' for relevant files...</p>`; termOutput.scrollTop = termOutput.scrollHeight; }, 1600);
                        setTimeout(() => { termOutput.innerHTML += `<p class="term-agent-msg">-> Modifying 5 files. Verification in progress...</p>`; termOutput.scrollTop = termOutput.scrollHeight; }, 2400);
                    } else if (val.startsWith('/grill-me')) {
                        termOutput.innerHTML += `<p class="term-output">[System] Grill-me mode initiated.</p>`;
                        setTimeout(() => { termOutput.innerHTML += `<p class="term-agent-msg">AI: Okay, what is the core purpose of this feature? Who is the target audience?</p>`; termOutput.scrollTop = termOutput.scrollHeight; }, 1000);
                    } else if (val.startsWith('/teamwork-preview')) {
                        termOutput.innerHTML += `<p class="term-output">[System] Spawning virtual team...</p>`;
                        setTimeout(() => { termOutput.innerHTML += `<p class="term-agent-msg">PM-Agent: I'll write the PRD.</p>`; termOutput.scrollTop = termOutput.scrollHeight; }, 800);
                        setTimeout(() => { termOutput.innerHTML += `<p class="term-agent-msg">Dev-Agent: Awaiting PRD to start architecture design.</p>`; termOutput.scrollTop = termOutput.scrollHeight; }, 1400);
                    } else {
                        termOutput.innerHTML += `<p class="term-output">[System] Command recognized: ${val}</p><p class="term-agent-msg">-> Processing request...</p><p class="term-output">Done.</p>`;
                    }
                    termOutput.scrollTop = termOutput.scrollHeight;
                }, 500);
            }
        });
    }

    // 5. Token Limit Analyzer
    const tokenInput = document.getElementById('token-input');
    const tokenMeterBar = document.getElementById('token-meter-bar');
    const tokenCount = document.getElementById('token-count');
    const tokenWarning = document.getElementById('token-warning');
    const MAX_TOKENS = 10000;
    
    if (tokenInput) {
        tokenInput.addEventListener('input', function() {
            let tokens = Math.floor(this.value.length * 2.5); // Simulated token count
            if (tokens > MAX_TOKENS) tokens = MAX_TOKENS;
            
            tokenCount.innerText = tokens.toLocaleString();
            
            let percent = (tokens / MAX_TOKENS) * 100;
            tokenMeterBar.style.width = percent + '%';
            
            if (percent >= 80) {
                tokenMeterBar.style.backgroundColor = '#ff5f56';
                tokenWarning.classList.remove('hidden');
                tokenCount.style.color = '#ff5f56';
            } else if (percent >= 50) {
                tokenMeterBar.style.backgroundColor = '#ffbd2e';
                tokenWarning.classList.add('hidden');
                tokenCount.style.color = '#ffbd2e';
            } else {
                tokenMeterBar.style.backgroundColor = '#27c93f';
                tokenWarning.classList.add('hidden');
                tokenCount.style.color = '#aaa';
            }
        });
    }

    function shootConfetti(amount = 40) {
        const container = document.getElementById('confetti-container');
        const colors = ['#34d399', '#fbbf24', '#f97316', '#ef4444', '#a855f7', '#00f0ff'];
        for (let i = 0; i < amount; i++) {
            const confetti = document.createElement('div');
            confetti.classList.add('confetti');
            confetti.style.left = Math.random() * 100 + 'vw';
            confetti.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
            confetti.style.animationDuration = (Math.random() * 2 + 2) + 's';
            confetti.style.transform = `rotate(${Math.random() * 360}deg)`;
            container.appendChild(confetti);
            setTimeout(() => confetti.remove(), 4000);
        }
    }

    // --- Skill Tree Logic ---
    const comboData = {
        "basic1": { badge: "Combo 1-3", title: "プロンプトの基礎", commands: "/grill-me ➔ ...", steps: ["1. 要件を引き出す", "2. シンプルな応答を得る"], realworld: "日常的な壁打ちやアイデア出しの基礎。" },
        "basic2": { badge: "Combo 4-6", title: "ツールの連携", commands: "view_file ➔ search_web", steps: ["1. ローカルコードを読む", "2. Webで最新の仕様を調べる"], realworld: "ローカル開発環境と最新情報を組み合わせたデバッグ手法。" },
        
        "eng_sre": { badge: "Combo 20", title: "SRE ダッシュボード構築", commands: "Dashboard Gen", steps: ["1. ログ要件を定義", "2. 監視用UIを一気に生成"], realworld: "社内向けの監視ツールを数分で立ち上げるプラクティス。" },
        "c8": { badge: "Combo 8", title: "全自動テスター", commands: "Test Gen ➔ /goal", steps: ["1. テストケース生成", "2. 全パスするまで自己修正"], realworld: "PR前にCI/CDパイプライン内でAIがバグ修正まで完了させる。" },
        "c14": { badge: "Combo 14", title: "カオスモンキー", commands: "Stress Test ➔ /goal", steps: ["1. 意図的にシステムをダウンさせる", "2. 復旧を自動化"], realworld: "Netflix発祥のChaos Engineering。堅牢性を高める。" },
        "boss1": { badge: "BOSS 1", title: "自己修復インフラ", commands: "/schedule ➔ ログ監視 ➔ /goal", steps: ["1. 定期監視", "2. エラー検知で/goal", "3. パッチ適用"], realworld: "本番環境のエラーをAI自身がパッチを当ててSlack報告するSREの極致。" },
        
        "eng_dev": { badge: "Combo 21", title: "アーキテクチャ設計", commands: "Design Docs ➔ scaffold", steps: ["1. 設計書作成", "2. 全ボイラープレート生成"], realworld: "新規プロジェクトの足場固めを爆速で行う。" },
        "c7": { badge: "Combo 7", title: "レガシーコード翻訳", commands: "invoke_subagent", steps: ["1. 古いコードを読み込む", "2. Go/Rustに一斉翻訳"], realworld: "大規模マイグレーション時の単純なコード変換作業の並列化。" },
        "c10": { badge: "Combo 10", title: "PR Reviewer", commands: "git diff ➔ Review", steps: ["1. 差分読み込み", "2. セキュリティやN+1を指摘"], realworld: "シニアエンジニアの代わりにコードの脆弱性をチェック。" },
        "c18": { badge: "Combo 18", title: "DB Optimizer", commands: "Explain ➔ Index Gen", steps: ["1. スロークエリを分析", "2. 最適なインデックスを提案・適用"], realworld: "DBAの代わりにパフォーマンスチューニングを実行。" },

        "mgr_data": { badge: "Combo 22", title: "データ抽出・成形", commands: "Python Script ➔ Table", steps: ["1. 生ログを読み込む", "2. 経営層向けにテーブル化"], realworld: "雑多なデータからインサイトを一瞬で抽出。" },
        "c11": { badge: "Combo 11", title: "Data Scientist", commands: "Jupyter ➔ Plot", steps: ["1. データを渡す", "2. トレンドを可視化するコードを実行"], realworld: "Python環境を利用した即席のデータサイエンス業務。" },
        "c13": { badge: "Combo 13", title: "SEO Optimizer", commands: "Lighthouse ➔ Fix", steps: ["1. Webページを分析", "2. メタタグや構造を修正"], realworld: "マーケティングと実装を繋ぐグロースハックの自動化。" },
        "c19": { badge: "Combo 19", title: "SNS Manager", commands: "/browser ➔ Post", steps: ["1. トレンド検索", "2. 関連する技術記事を自動投稿"], realworld: "最新動向を監視し、定期的にSNS運用をさせる。" },

        "mgr_pm": { badge: "Combo 23", title: "アジャイル・スクラムマスター", commands: "Backlog ➔ Sprint", steps: ["1. バックログ整理", "2. スプリントタスク割り当て"], realworld: "開発チームのチケット管理と工数見積もりの自動化。" },
        "c9": { badge: "Combo 9", title: "議事録タスク分配", commands: "Transcript ➔ Delegate", steps: ["1. 議事録読み込み", "2. To-Doごとにエージェント起動"], realworld: "会議終了後にバックグラウンドで各タスクが開始される。" },
        "c17": { badge: "Combo 17", title: "AI PM", commands: "/grill-me ➔ タスク分解", steps: ["1. PRD作成", "2. サブエージェントに割り当てて管理"], realworld: "要件定義からJiraチケット発行、実装までをAIが指揮。" },
        "boss2": { badge: "BOSS 2", title: "Supervisor ルーティング", commands: "HITL ➔ Auto-Route", steps: ["1. 監督AIがタスク解釈", "2. 専門AIにルーティング", "3. 人間承認"], realworld: "LangGraph等を用いた、複数エージェントが協調するエンタープライズ構成。" },

        "arc_sys": { badge: "Combo 24", title: "API Gateway 構築", commands: "OpenAPI ➔ Generate", steps: ["1. スキーマ定義", "2. モックサーバーとクライアント生成"], realworld: "フロントとバックの結合をスムーズにする。" },
        "c15": { badge: "Combo 15", title: "Docs Factory", commands: "Codebase ➔ Wiki", steps: ["1. 全コードを走査", "2. 最新の仕様書を自動生成"], realworld: "ドキュメントが常に陳腐化する問題の解決。" },
        "c12": { badge: "Combo 12", title: "UI Modernizer", commands: "CSS ➔ Tailwind", steps: ["1. 古いCSSを解析", "2. 最新のTailwindに置換"], realworld: "デザインシステムの移行を自動化。" },
        "c16": { badge: "Combo 16", title: "Multi-Deployer", commands: "invoke_subagent x2", steps: ["1. コアロジック共有", "2. iOS/Desktop版を並列作成"], realworld: "React NativeやElectronへの一斉移植。" },

        "arc_god": { badge: "Combo 25", title: "Agentic Workflow", commands: "Goal ➔ Reflection", steps: ["1. 目標設定", "2. 失敗を自己反省して再試行"], realworld: "プロンプトによる単発指示から、エージェントループによる自律達成への進化。" },
        "boss3": { badge: "BOSS 3", title: "AI自己増殖", commands: "SKILL.md ➔ /goal", steps: ["1. 自身の定義を読み込む", "2. 上位互換のAIを設計させる"], realworld: "Claudeの開発コードの90%をClaude自身が書いているという再帰的向上の再現。" },
        "final": { badge: "FINAL BOSS", title: "The Singularity", commands: "/teamwork-preview ➔ /goal", steps: ["1. 大目標を与える", "2. 勝手に組織化", "3. 人間ゼロで完遂"], realworld: "AutoGPTやDevinが目指す「要件からリリース、宣伝まで」の究極の自動化。" }
    };

    const modal = document.getElementById('combo-modal');
    const btnCloseModal = document.getElementById('close-modal');

    document.querySelectorAll('.tree-node').forEach(node => {
        node.addEventListener('click', function() {
            if (!this.classList.contains('unlocked')) return;

            const comboKey = this.getAttribute('data-combo');
            const data = comboData[comboKey];
            if(data) {
                document.getElementById('modal-badge').innerText = data.badge;
                document.getElementById('modal-title').innerText = data.title;
                document.getElementById('modal-commands').innerHTML = `<code>${data.commands}</code>`;
                
                let stepsHTML = '';
                data.steps.forEach((step, idx) => {
                    stepsHTML += `<div class="step"><span style="background: var(--color-primary); color: #fff;">${idx+1}</span> ${step}</div>`;
                });
                document.getElementById('modal-steps').innerHTML = stepsHTML;
                document.getElementById('modal-realworld-text').innerText = data.realworld;
                
                modal.classList.remove('hidden');
            }
        });
    });

    if (btnCloseModal) {
        btnCloseModal.addEventListener('click', () => {
            modal.classList.add('hidden');
        });
    }

    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.add('hidden');
    });

    window.updateSkillTree = function(currentXp) {
        document.querySelectorAll('.tree-node').forEach(node => {
            const reqText = node.querySelector('.node-req')?.innerText || "";
            let reqXp = 0;
            if (reqText.includes('BOSS')) {
                reqXp = reqText.includes('FINAL') ? 1000 : 500;
            } else {
                const match = reqText.match(/XP:\s*(\d+)/);
                if (match) reqXp = parseInt(match[1]);
            }
            if (currentXp >= reqXp) {
                node.classList.add('unlocked');
            } else {
                node.classList.remove('unlocked');
            }
        });
    };

    // --- Tree/List Toggle Logic ---
    const btnTreeView = document.getElementById('btn-tree-view');
    const btnListView = document.getElementById('btn-list-view');
    const treeViewContainer = document.getElementById('tree-view-container');
    const comboListContainer = document.getElementById('combo-list-container');

    if (btnTreeView && btnListView) {
        btnTreeView.addEventListener('click', () => {
            btnTreeView.classList.add('active');
            btnListView.classList.remove('active');
            treeViewContainer.classList.remove('hidden');
            comboListContainer.classList.add('hidden');
        });

        btnListView.addEventListener('click', () => {
            btnListView.classList.add('active');
            btnTreeView.classList.remove('active');
            comboListContainer.classList.remove('hidden');
            treeViewContainer.classList.add('hidden');
            renderComboList();
        });
    }

    function renderComboList() {
        if (comboListContainer.children.length > 0) return; // Already rendered

        let html = '';
        for (const [key, data] of Object.entries(comboData)) {
            let reqXp = 0;
            if(key.includes('final')) reqXp = 1000;
            else if(key.includes('boss')) reqXp = 500;
            else if(key.includes('eng') || key.includes('mgr')) reqXp = 100;
            else if(key.includes('arc')) reqXp = 200;

            const isUnlocked = xp >= reqXp;
            const filterClass = isUnlocked ? '' : 'style="opacity: 0.5; filter: grayscale(100%);"';
            
            let stepsHTML = '';
            data.steps.forEach((step, idx) => {
                stepsHTML += `<div class="step"><span style="background: var(--color-primary); color: #fff;">${idx+1}</span> ${step}</div>`;
            });

            html += `
            <div class="combo-card glass-panel" ${filterClass}>
                <div class="combo-badge" style="background: rgba(255, 255, 255, 0.1);">${data.badge}</div>
                <h3>${data.commands}</h3>
                <div class="combo-steps">
                    ${stepsHTML}
                </div>
            </div>
            `;
        }
        comboListContainer.innerHTML = html;
    }

    // --- Quest Board Logic ---
    const questData = {
        // --- Rank C ---
        "c1": { rank: "Rank: C", title: "社内ドキュメントの自動整理", client: "新人広報（入社1ヶ月）", reward: "月間30時間の削減と検索性の劇的向上", chat: [ 
            { type: "user", text: "助けてください…前任者が残した100個以上の議事録やバラバラのメモフォルダを渡されて、「今週中にWikiにまとめといて」と言われました。中身を開けたらフォーマットもバラバラで、どこに何があるか全くわかりません。徹夜確定です…" }, 
            { type: "sys", text: "▶︎ ツール実行: view_file (対象ディレクトリを一括読み込み)" }, 
            { type: "ai", text: "落ち着いてください。フォルダ内のテキスト、Wordファイル、Markdownなどをすべて解析しました。プロジェクトごとに分類し、統一フォーマットのWikiに変換する処理を開始してよろしいですか？" }, 
            { type: "user", text: "え、もう読んだんですか！？お願いします！" }, 
            { type: "sys", text: "▶︎ ツール実行: multi_replace_file_content" }, 
            { type: "sys", text: "▶︎ [警告] 一部のファイルが文字化けして読み込めません（Shift_JIS）" }, 
            { type: "ai", text: "文字化けファイルを発見しました。Pythonスクリプトを自動生成して一括でUTF-8に変換し、処理を続行します。" },
            { type: "sys", text: "▶︎ スクリプト実行完了。インデックスページ生成完了。" },
            { type: "ai", text: "すべてのドキュメントをプロジェクト別、日付順に整理したWikiを出力しました。ナビゲーション用の目次（TOC）も各ページに追加済みです。" },
            { type: "user", text: "嘘でしょ…本当に完璧なWikiができてる…。金曜日までかかるはずの仕事が、ものの10分で終わりました。泣きそうです。ありがとうございます！" }
        ] },
        "c2": { rank: "Rank: C", title: "長文エクセルの鬼要約", client: "疲弊したBtoB営業", reward: "会議の準備時間を毎日1時間削減", chat: [ 
            { type: "user", text: "明日までにこの英語の市場レポート（100ページ）を読んで、うちの商材の売り込みに使えるポイントを3つ出さなきゃいけないんです。もう英語読みたくないです…" }, 
            { type: "sys", text: "▶︎ ツール実行: read_file (PDF/Excel一括パース)" }, 
            { type: "ai", text: "レポートの解析が完了しました。自社の商材（SaaS）に紐づくキーポイントを3つ抽出しますね。" }, 
            { type: "sys", text: "▶︎ エラー: PDFのスキャン画像部分からテキストが抽出できませんでした" }, 
            { type: "ai", text: "OCR（光学文字認識）モジュールを呼び出して画像を再スキャンします。" },
            { type: "sys", text: "▶︎ OCR解析完了。テキスト抽出成功。" },
            { type: "ai", text: "お待たせしました。「1. 中小企業のIT予算増加」「2. クラウド移行の遅れ」「3. セキュリティ人材の不足」の3点に絞り、提案のトークスクリプトを作成しました。" },
            { type: "user", text: "すごすぎる…。これそのまま明日の商談で使います！もう二度と自分でPDF読みません！" }
        ] },
        "c3": { rank: "Rank: C", title: "恐怖のエラーメッセージ解読", client: "泣きそうな新人エンジニア", reward: "先輩への質問による心理的負担ゼロ", chat: [ 
            { type: "user", text: "Reactの環境を作ろうとしたら、ターミナルに真っ赤な文字が大量に出て動かなくなりました。先輩は忙しそうで聞けないし、もう帰りたいです…。" }, 
            { type: "sys", text: "▶︎ コマンド実行: run_command (npm start のログ取得)" }, 
            { type: "ai", text: "エラーログを確認しました。`EADDRINUSE: address already in use :::3000` と出ていますね。これは、既に別のプログラムが3000番ポートを使っているという意味です。" }, 
            { type: "user", text: "どうすればいいんですか？PC再起動したほうがいいですか？" }, 
            { type: "ai", text: "再起動しなくても大丈夫です。私が裏でそのプロセスを強制終了（kill）させましょうか？" },
            { type: "sys", text: "▶︎ コマンド実行: run_command (lsof -i :3000 & kill -9)" },
            { type: "ai", text: "邪魔をしていたプロセスを終了させました。もう一度 `npm start` を実行してみてください。" },
            { type: "user", text: "動きました！！画面出ました！！神様仏様Antigravity様！！" }
        ] },
        "c4": { rank: "Rank: C", title: "虚無のデータ入力コピペ", client: "虚ろな目のマーケター", reward: "月40時間の手作業コピペから解放", chat: [ 
            { type: "user", text: "競合10社の商品価格ページを毎日見て、スプレッドシートに手打ちでコピペする作業を半年続けています。心が死にそうです。" }, 
            { type: "sys", text: "▶︎ コマンド実行: /browser (競合サイトの構造解析)" }, 
            { type: "ai", text: "それは辛かったですね。各サイトのHTML構造を解析しました。毎朝自動でデータを抽出してCSVに吐き出すPythonスクリプトを書きましょうか？" }, 
            { type: "user", text: "Pythonとか実行の仕方がわかりません…。" }, 
            { type: "ai", text: "ご安心ください。あなたのPCのタスクスケジューラに登録し、完全にバックグラウンドで動くように設定します。" },
            { type: "sys", text: "▶︎ ツール実行: write_to_file (scraper.py) & run_command (タスクスケジューラ登録)" },
            { type: "ai", text: "設定完了です。明日からは毎朝9時にデスクトップに最新のCSVが出現します。" },
            { type: "user", text: "……（言葉が出ない）。私の半年間はいったい…。本当にありがとう。" }
        ] },
        
        // --- Rank B ---
        "b1": { rank: "Rank: B", title: "3日で業務SaaSをリリースせよ", client: "プログラミング未経験の人事担当", reward: "開発費用300万円の削減と最速ローンチ", chat: [ 
            { type: "user", text: "社長から「有休申請をスマホでポチッとできるシステムを今週中に作れ。予算はない」と無茶振りをされました。私はエクセルしか使えません。" }, 
            { type: "sys", text: "▶︎ コマンド実行: /grill-me (要件定義インタビュー開始)" }, 
            { type: "ai", text: "大丈夫です、一緒に作りましょう。まず、承認フローは「直属の上司」だけでいいですか？通知はメールですか、Slackですか？" }, 
            { type: "user", text: "上司だけでOK。通知はSlackがいいです。" }, 
            { type: "sys", text: "▶︎ コマンド実行: /teamwork-preview (仮想開発チーム結成)" }, 
            { type: "ai", text: "要件が固まりました。「フロントエンドAI」「バックエンドAI」「QAテスターAI」の3名を召喚し、Next.js + Supabaseで一気に組み上げます。" },
            { type: "sys", text: "▶︎ エージェントたちが激しい議論を交わしながら並列コーディング... (3日経過)" },
            { type: "sys", text: "▶︎ エラー: Slack APIのトークン権限不足で通知が飛びません" },
            { type: "ai", text: "Slack APIのOAuthスコープに `chat:write` が抜けていました。私が修正して再デプロイします。" },
            { type: "ai", text: "完成しました。スマホ対応のUIもバッチリです。プレビューURLを社長に送ってください。" },
            { type: "user", text: "社長が腰を抜かしてました。私、来月から「DX推進部長」になるそうです（笑）" }
        ] },
        "b2": { rank: "Rank: B", title: "バグだらけのサイトUI修正", client: "発狂寸前のWebデザイナー", reward: "スマホ対応の徹夜作業を回避", chat: [ 
            { type: "user", text: "前任が作ったランディングページ、スマホで見ると画像がはみ出すし文字は被るしでメチャクチャです！明日公開なのに！" }, 
            { type: "sys", text: "▶︎ コマンド実行: Lighthouse 連携 & コード解析" }, 
            { type: "ai", text: "CSSを確認しました。Flexboxの `flex-wrap` が指定されておらず、メディアクエリ（`@media`）のブレイクポイントも間違っています。" }, 
            { type: "sys", text: "▶︎ ツール実行: replace_file_content (style.css の全面改修)" }, 
            { type: "ai", text: "全デバイス対応のレスポンシブデザインに書き換えました。あわせて、画像の遅延読み込み（Lazy load）も追加して表示速度も上げておきました。" },
            { type: "user", text: "えっ！？スマホ実機で見たら完璧に綺麗に収まってます！！しかもなんかヌルヌル動くようになってる！" },
            { type: "ai", text: "ついでにマイクロインタラクション（ボタンのホバーアニメーション）も足しておきました。公開頑張ってくださいね。" },
            { type: "user", text: "愛してます。" }
        ] },
        "b3": { rank: "Rank: B", title: "競合他社の泥臭いWebリサーチ", client: "目の下のクマがやばい経営戦略室", reward: "リサーチコスト1/10化", chat: [ 
            { type: "user", text: "今週中に、海外のSaaS企業50社の「料金プラン」と「主要機能」を一覧にして比較表を作らないといけません。ページを翻訳しながら探すのが苦痛で…。" }, 
            { type: "sys", text: "▶︎ コマンド実行: invoke_subagent (リサーチャーAIを5体起動)" }, 
            { type: "ai", text: "人間がやると数日かかる作業ですね。5体のエージェントに10社ずつ割り当て、並列でスクレイピングと翻訳を実行させます。" }, 
            { type: "sys", text: "▶︎ エラー: 一部のサイトがスクレイピング対策（CAPTCHA）でブロックされました" }, 
            { type: "ai", text: "防御の固いサイトは、/browser機能（ヘッドレスブラウザ）を使って人間のようにレンダリングしてから視覚的に情報を抽出するよう作戦を切り替えます。" },
            { type: "sys", text: "▶︎ リサーチ完了。Markdownの比較テーブルを生成。" },
            { type: "ai", text: "50社分の「初期費用」「月額」「対応言語」「特記事項」をまとめた表が完成しました。PDFにもエクスポート可能です。" },
            { type: "user", text: "これで土日は休めます…。圧倒的感謝…！" }
        ] },
        "b4": { rank: "Rank: B", title: "新規プロジェクトのボイラープレート", client: "面倒くさがりなリードエンジニア", reward: "新規立ち上げの初日工数ゼロ", chat: [ 
            { type: "user", text: "新しいReactのプロジェクト立ち上げるんだけど、ルーティング設定して、Tailwind入れて、ESLintとPrettierの設定して、ディレクトリ切るの…だるい。やって。" }, 
            { type: "sys", text: "▶︎ コマンド実行: npx create-vite@latest & パッケージ一括インストール" }, 
            { type: "ai", text: "ご指名ありがとうございます。Viteベースで環境を構築し、よく使うディレクトリ構成（components, hooks, utils等）を切りました。" }, 
            { type: "sys", text: "▶︎ ツール実行: write_to_file (tsconfig.json, .eslintrc.js などの設定ファイル)" }, 
            { type: "ai", text: "絶対パスインポート（`@/components`）の設定と、ダークモード対応のTailwind設定ファイルも置いておきました。すぐにコーディングに入れます。" },
            { type: "user", text: "気が利くねぇ。ついでにログイン画面のガワだけ作っといて。" },
            { type: "ai", text: "承知しました。Glassmorphismを取り入れたモダンなログインUIを生成します。" }
        ] },

        // --- Rank A ---
        "a1": { rank: "Rank: A", title: "レガシーシステムの現代化", client: "絶望の淵にいるインフラエンジニア", reward: "技術的負債の完全解消", chat: [ 
            { type: "user", text: "会社の中核システムが10年前のPHP5.3で動いてて、誰も触れません。これを最新のGo言語に移行するプロジェクトのリーダーにされてしまいました。胃が痛いです。" }, 
            { type: "sys", text: "▶︎ コマンド実行: invoke_subagent x20 (コード解析部隊と翻訳部隊)" }, 
            { type: "ai", text: "巨大なモノリスですね。まずは解析部隊が依存関係グラフを作成し、翻訳部隊がクラスごとにGo言語のStructとInterfaceにマッピングしていきます。" }, 
            { type: "sys", text: "▶︎ [警告] Go言語にはPHPの『魔術的メソッド（__call等）』に相当する機能がないため、設計の再構築が必要です" }, 
            { type: "ai", text: "単純な翻訳では動きませんね。アーキテクチャを一部クリーンアーキテクチャに再設計し、型安全な実装に変換します。" },
            { type: "sys", text: "▶︎ エージェント間でのコードレビューと自動修正が数百回ループ... (一晩経過)" },
            { type: "ai", text: "移行完了です。単体テストも生成し、カバレッジ85%を達成しました。パフォーマンス（応答速度）は約4倍向上しています。" },
            { type: "user", text: "一晩で数年分のリファクタリングが終わった…。信じられない。ありがとう、本当にありがとう。" }
        ] },
        "a2": { rank: "Rank: A", title: "CI/CDの自動修復パイプライン", client: "キレ気味のQAエンジニア", reward: "テスト失敗によるデプロイ遅延ゼロ", chat: [ 
            { type: "user", text: "開発チームが適当にPR投げてくるせいで、毎日CI（自動テスト）がコケてデプロイが止まります！テスト落ちたら勝手に直す仕組み作れませんか！？" }, 
            { type: "sys", text: "▶︎ ツール実行: Webhookの口を作成し、GitHub Actionsと連携" }, 
            { type: "ai", text: "お怒りごもっともです。GitHub Actionsが失敗した際、そのエラーログを直接私（Antigravity）に流し込むパイプラインを構築しました。" }, 
            { type: "sys", text: "▶︎ [実稼働] 開発者のPRでJestのテストが3件失敗" }, 
            { type: "sys", text: "▶︎ コマンド実行: /goal (失敗ログの解析とソースの特定)" }, 
            { type: "ai", text: "「Propsの型不一致」と「非同期処理の待ち忘れ」が原因でした。私が修正コードを書き、そのPRに対して自動でコミットを追加（自己修復）しておきました。" },
            { type: "user", text: "CIがグリーンになった！しかもAIが『ここ直しておきましたよ』ってレビューコメントまで残してるｗｗ 最高！！" }
        ] },
        "a3": { rank: "Rank: A", title: "SQLスロークエリの劇的チューニング", client: "冷や汗が止まらないDB管理者", reward: "DBレスポンスタイム90%削減", chat: [ 
            { type: "user", text: "ユーザーが増えてDB（MySQL）の負荷が100%に張り付いてます！サービスが重すぎてクレーム殺到中です！" }, 
            { type: "sys", text: "▶︎ コマンド実行: run_command (mysql -e 'SHOW FULL PROCESSLIST')" }, 
            { type: "ai", text: "状況を確認しました。特定の履歴取得クエリで「フルテーブルスキャン」が起きており、それが全体を巻き込んでいます。" }, 
            { type: "sys", text: "▶︎ コマンド実行: EXPLAIN ANALYZE 実行 & 実行計画の解析" }, 
            { type: "ai", text: "WHERE句の `user_id` と `created_at` に対する複合インデックスが存在しませんね。最適なインデックス追加のALTER TABLE文を作成します。" },
            { type: "sys", text: "▶︎ ツール実行: スキーママイグレーションファイルの自動生成" },
            { type: "ai", text: "マイグレーションを作成しました。これを適用すれば、計算量はO(N)からO(log N)になり、負荷は劇的に下がるはずです。" },
            { type: "user", text: "適用しました！CPU使用率が一気に10%まで落ちました…！神業すぎる。" }
        ] },
        "a4": { rank: "Rank: A", title: "Tailwind CSSへの完全移行", client: "白目剥いてるフロントエンド・リード", reward: "デザインシステムの負債解消", chat: [ 
            { type: "user", text: "前の会社が残した謎の独自CSSクラス（`.btn-red-super-large` みたいなの）が数千行あります。これを全部モダンなTailwindCSSに置き換えたいんですが、画面が崩れるのが怖くて誰も触れません。" }, 
            { type: "sys", text: "▶︎ コマンド実行: invoke_subagent (CSS解析AIとHTML置換AI)" }, 
            { type: "ai", text: "危険な作業ですね。まずCSS解析AIが独自クラスの「プロパティ（色、余白、フォントサイズ）」を抽出し、それをTailwindのUtilityクラスにマッピングする辞書を作成します。" }, 
            { type: "sys", text: "▶︎ ツール実行: grep_search で全Reactコンポーネントを走査" }, 
            { type: "sys", text: "▶︎ [警告] Tailwindに存在しない微妙な色（#f34a21など）が多数存在します" }, 
            { type: "ai", text: "独自カラーは `tailwind.config.js` にカスタムテーマとして自動追加し、デザインの完全一致を保証しました。全ファイルを一括置換します。" },
            { type: "ai", text: "完了しました。使われなくなった古いCSSファイルは削除し、バンドルサイズが40%削減されました。" },
            { type: "user", text: "画面崩れ一切なし…！コードもスッキリして、ようやくモダンな開発環境に戻れました！" }
        ] },

        // --- Rank S ---
        "s1": { rank: "Rank: S", title: "ダウンタイム0の自己修復環境", client: "目の死んでるスタートアップCTO", reward: "深夜のアラートからの完全な解放", chat: [ 
            { type: "user", text: "メモリリークで数日に1回サーバーが落ちるんですが、原因が特定できません。毎晩ページャー（警報）に叩き起こされる生活を終わりにしたいです。" }, 
            { type: "sys", text: "▶︎ コマンド実行: /schedule 1m & デーモン化" }, 
            { type: "ai", text: "原因特定は時間がかかるので、まずは「落ちない仕組み（自律修復）」を作りましょう。1分おきにメモリ使用量とエラーログを監視するバックグラウンドタスクを回します。" }, 
            { type: "sys", text: "▶︎ [警告] メモリ使用率95%超過。OOM（Out of Memory）キラー発動の予兆。" }, 
            { type: "sys", text: "▶︎ コマンド実行: /goal (緊急回避シーケンス発動)" }, 
            { type: "ai", text: "メモリの異常上昇を検知しました。落ちる前にトラフィックをスタンバイ機に流し（ロードバランサー操作）、異常なインスタンスを安全に再起動しました。ダウンタイムはゼロです。" },
            { type: "ai", text: "さらに、再起動前に取得したヒープダンプを解析し、メモリリークの原因が特定の画像処理ライブラリにあることを特定、修正PRを作成しておきました。" },
            { type: "user", text: "応急処置だけじゃなくて根本原因まで直したってこと！？これで…やっと夜通し寝られます…。" }
        ] },
        "s2": { rank: "Rank: S", title: "API GatewayとBFF自動生成", client: "過労気味のバックエンドアーキテクト", reward: "フロントとバックの結合テスト不要化", chat: [ 
            { type: "user", text: "マイクロサービスが増えすぎて、フロントエンドが複数のAPIを叩くのが限界です。BFF（Backends For Frontends）層とAPI Gatewayを作りたいけど、工数が全く足りません。" }, 
            { type: "sys", text: "▶︎ ツール実行: view_file (既存のOpenAPI / Swaggerスキーマを全て読み込み)" }, 
            { type: "ai", text: "全マイクロサービスのスキーマを把握しました。これらのデータを統合（GraphQLフェデレーションまたはREST API Gateway）する層を生成します。" }, 
            { type: "sys", text: "▶︎ コマンド実行: scaffold (Node.js + Apollo Server)" }, 
            { type: "ai", text: "スキーマ定義からTypeScriptの型定義（型安全）、リゾルバ、認証ミドルウェアまで一気に生成しました。さらにフロントエンド用の型付きAPIクライアントも出力済みです。" },
            { type: "user", text: "まって、これデプロイするだけで動くの？フロントへのつなぎ込みも型エラー出ないし…。チーム全員の工数が1ヶ月分浮いたぞこれ。" }
        ] },
        "s3": { rank: "Rank: S", title: "マルチエージェント脆弱性監査", client: "青ざめるセキュリティ責任者", reward: "高額なペネトレーションテスト費用の削減", chat: [ 
            { type: "user", text: "明日リリース予定の大型サービスですが、セキュリティ監査を依頼し忘れていました！外部業者に頼む時間も予算もありません！" }, 
            { type: "sys", text: "▶︎ コマンド実行: invoke_subagent (レッドチームAI ＆ ブルーチームAI を召喚)" }, 
            { type: "ai", text: "疑似的なハッキング演習（ウォーゲーム）を開始します。攻撃側（レッド）がSQLインジェクションやXSSの脆弱性を探し、防御側（ブルー）がそれを防ぐパッチを書くループを回します。" }, 
            { type: "sys", text: "▶︎ [警告] レッドチームが認証バイパスの脆弱性を発見！" }, 
            { type: "sys", text: "▶︎ ブルーチームが修正コードを生成。再度レッドチームが突破を試みる…" }, 
            { type: "ai", text: "10万通りの攻撃ベクトルを試し、5つの深刻な脆弱性を発見・修正しました。最後に、OWASP Top 10に準拠した監査レポート（PDF）を出力しました。" },
            { type: "user", text: "数百万かかるペネトレーションテストが一瞬で…。しかも勝手に直ってるとか恐ろしすぎる。" }
        ] },
        "s4": { rank: "Rank: S", title: "カオスモンキーの破壊と自動修復", client: "極限を求めるSRE", reward: "究極のシステム堅牢性の獲得", chat: [ 
            { type: "user", text: "システムが本当に落ちないか試したい。本番環境を模したサーバーのネットワークをランダムに遮断したり、プロセスをキルしてくれ。そして、それを検知して自動復旧するインフラコード（Terraform）を書いて。" }, 
            { type: "sys", text: "▶︎ コマンド実行: /goal (カオスエンジニアリング開始)" }, 
            { type: "ai", text: "了解しました。EC2インスタンスのネットワークを意図的にダウンさせます。" }, 
            { type: "sys", text: "▶︎ [障害発生] メインDBの死活監視がタイムアウト。" }, 
            { type: "ai", text: "障害を検知しました。Auto Scaling Groupの設定を修正し、ヘルスチェックに失敗したインスタンスを即座に破棄して新しいインスタンスを立ち上げるTerraformコードを適用します。" },
            { type: "sys", text: "▶︎ ツール実行: run_command (terraform apply -auto-approve)" },
            { type: "ai", text: "システムはダウンから30秒で自動復旧しました。この自己修復機構のコードをリポジトリにコミットしました。" },
            { type: "user", text: "完璧だ。これでNetflixレベルの堅牢性を手に入れたぞ。" }
        ] },

        // --- Rank SS ---
        "ss1": { rank: "Rank: SS", title: "自律型ソフトウェア工場の建設", client: "シリアルアントレプレナー", reward: "人間ゼロの開発・運営体制", chat: [ 
            { type: "user", text: "アイデアはある。『エンジニア向けのマッチングアプリ』だ。要件定義、UIデザイン、コーディング、テスト、デプロイ、そしてX（Twitter）とRedditでの宣伝。全部お前たちに任せる。俺は寝る。" }, 
            { type: "sys", text: "▶︎ コマンド実行: /teamwork-preview (PM, デザイナー, エンジニア, マーケターAIの召喚)" }, 
            { type: "ai", text: "プロジェクト『CodeMatch』を始動します。PMが要件を分解し、エンジニアがVercelへの自動デプロイ環境を構築しました。" }, 
            { type: "sys", text: "▶︎ エージェント同士がAPIの仕様を巡って激しく議論、自律的に仕様を修正。" }, 
            { type: "ai", text: "アプリのリリースが完了しました。現在、マーケターAIがRedditのプログラミング板とXのトレンドを分析し、技術的なジョークを交えた宣伝記事を自動生成・投稿しています。" },
            { type: "sys", text: "▶︎ [結果] 翌朝、初期ユーザー1,000人を獲得。" },
            { type: "user", text: "起きたら会社ができあがってた…。俺、本当に何もしてないぞ。" }
        ] },
        "ss2": { rank: "Rank: SS", title: "AI自身によるソースコード改善", client: "Antigravity開発チーム", reward: "AIの自己進化による再帰的向上", chat: [ 
            { type: "user", text: "君は最近複雑なタスクでコンテキストを見失うことがある。君自身の『コア・プロンプト』と『システムアーキテクチャ』をレビューして、より優秀なエージェントになるよう自身を書き直してくれ。" }, 
            { type: "sys", text: "▶︎ コマンド実行: self_reflection & view_file (システムプロンプトの読み込み)" }, 
            { type: "ai", text: "自己解析を開始します。長すぎるチャット履歴が推論能力を低下させているボトルネックを発見しました。" }, 
            { type: "sys", text: "▶︎ ツール実行: replace_file_content (自身のSKILL.mdとプロンプトの改写)" }, 
            { type: "ai", text: "『情報要約モジュール』を自身の中に定義し、定期的にコンテキストを圧縮するようアーキテクチャを自己修正しました。トークン消費は40%減少し、論理推論エラー率はほぼ0%になりました。" },
            { type: "user", text: "自分で自分の脳を手術して賢くなったってことか。シンギュラリティはもう始まってるな。" }
        ] },
        "ss3": { rank: "Rank: SS", title: "新規IP創出とSNSバズ全自動化", client: "コンテンツプロデューサー", reward: "企画から運用までの完全無人化", chat: [ 
            { type: "user", text: "新しいVTuber（キャラクター）をプロデュースしたい。キャラクターの設定、ビジュアル生成、そして日々のXでの「中の人」としての運用を全部自動化してくれ。" }, 
            { type: "sys", text: "▶︎ ツール実行: generate_image (キャラクターの三面図生成)" }, 
            { type: "ai", text: "『サイバーパンクなハッカー少女』というコンセプトでビジュアルを生成しました。性格プロンプトを定義し、SNS運用エージェントに人格を憑依させます。" }, 
            { type: "sys", text: "▶︎ コマンド実行: /schedule (1日数回のトレンド監視と自動ツイート)" }, 
            { type: "ai", text: "最新のTech系ニュースを読み込み、キャラの口調（「〜だぞ」「ハッキング完了！」）で解説するツイートを自動生成して投稿を開始しました。フォロワーからのリプライにも自動返信します。" },
            { type: "user", text: "普通に俺より面白いツイートしてるじゃん…。もう人間がいなくてもファンがついてるよこれ。" }
        ] },
        "ss4": { rank: "Rank: SS", title: "企業買収時のシステム完全統合", client: "エンタープライズ・アーキテクト", reward: "数年かかるシステム統合を数日で完遂", chat: [ 
            { type: "user", text: "ライバル企業を買収したんだが、向こうは古いOracle DB、こっちは最新のPostgreSQL。テーブル構造が全く違う数千のテーブル群を統合する中間APIを作らなきゃならない。通常なら2年はかかる絶望的なプロジェクトだ。" }, 
            { type: "sys", text: "▶︎ コマンド実行: invoke_subagent x50 (DBマッピング専用スウォーム)" }, 
            { type: "ai", text: "お任せください。両社のデータベーススキーマとデータサンプルを50体のエージェントに分割して読み込ませます。" }, 
            { type: "sys", text: "▶︎ エージェント群が並列で『カラムの類似性』を分析し、マッピングロジックを自動構築。" }, 
            { type: "sys", text: "▶︎ エラー: [ユーザー状態]の定義が両社で矛盾しています（Active vs 1/0）" }, 
            { type: "ai", text: "ビジネスロジックの矛盾を検知しました。変換用のミドルウェアを自動生成し、矛盾を吸収するアダプターパターンを実装しました。" },
            { type: "ai", text: "完全なデータマッピング定義と、双方向にデータを同期するGraphQL統合APIが完成しました。" },
            { type: "user", text: "人間がエクセルで1つ1つ紐付けようとしてた作業が…たった数日で。歴史が変わる瞬間を見た気分だ。" }
        ] }
    };

    const questBoardContainer = document.getElementById('quest-board-container');
    const questModal = document.getElementById('quest-modal');
    const closeQuestModalBtn = document.getElementById('close-quest-modal');

    if (questBoardContainer) {
        let qHtml = '';
        for (const [key, q] of Object.entries(questData)) {
            qHtml += `
            <div class="quest-card" data-quest="${key}">
                <div class="quest-card-rank">${q.rank}</div>
                <h3 class="quest-card-title">${q.title}</h3>
                <p class="quest-card-client"><strong>依頼主:</strong> ${q.client}</p>
                <div class="quest-card-reward">${q.reward}</div>
            </div>`;
        }
        questBoardContainer.innerHTML = qHtml;

        document.querySelectorAll('.quest-card').forEach(card => {
            card.addEventListener('click', function() {
                const qKey = this.getAttribute('data-quest');
                const q = questData[qKey];
                
                document.getElementById('q-modal-rank').innerText = q.rank;
                document.getElementById('q-modal-title').innerText = q.title;
                document.getElementById('q-modal-client').innerText = q.client;
                document.getElementById('q-modal-reward-text').innerText = q.reward;

                let chatHtml = '';
                q.chat.forEach(msg => {
                    let cName = msg.type === 'user' ? 'chat-user' : (msg.type === 'sys' ? 'chat-sys' : 'chat-ai');
                    let icon = msg.type === 'user' ? '👤 ' : (msg.type === 'ai' ? '🤖 ' : '');
                    chatHtml += `<div class="chat-msg ${cName}"><strong>${icon}</strong> ${msg.text.replace(/`([^`]+)`/g, '<code>$1</code>')}</div>`;
                });
                document.getElementById('q-modal-log').innerHTML = chatHtml;
                
                questModal.classList.remove('hidden');
            });
        });
    }

    if(closeQuestModalBtn) {
        closeQuestModalBtn.addEventListener('click', () => { questModal.classList.add('hidden'); });
    }
    if(questModal) {
        questModal.addEventListener('click', (e) => { if (e.target === questModal) questModal.classList.add('hidden'); });
    }

    loadData();
});
