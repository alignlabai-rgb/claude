document.addEventListener('DOMContentLoaded', () => {
    console.log('Vibe Coding Guide Initialized');

    // Smooth navigation
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            document.querySelector(this.getAttribute('href')).scrollIntoView({
                behavior: 'smooth'
            });
        });
    });

    // Simple scroll animation observer
    const observerOptions = {
        threshold: 0.1
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target); // Only animate once
            }
        });
    }, observerOptions);

    document.querySelectorAll('.card, .record-card, .section-title, .hero .content').forEach(el => {
        observer.observe(el);
    });

    // Add visible class styling via CSS injection or just apply directly
    const style = document.createElement('style');
    style.textContent = `
        .visible {
            opacity: 1 !important;
            transform: translateY(0) !important;
        }
    `;
    // Playground Logic
    const vibeInput = document.getElementById('vibe-input');
    const terminalContent = document.getElementById('terminal-content');
    const previewDisplay = document.getElementById('preview-display');

    if (vibeInput) {
        vibeInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && vibeInput.value.trim() !== '') {
                const cmd = vibeInput.value;
                vibeInput.value = '';
                processCommand(cmd);
            }
        });
    }

    function addTerminalLine(text, type = 'output') {
        const line = document.createElement('div');
        line.className = 'line';
        if (type === 'input') line.innerHTML = `<span class="prompt">></span> ${text}`;
        else line.textContent = text;
        terminalContent.insertBefore(line, terminalContent.querySelector('.input-line'));
        terminalContent.scrollTop = terminalContent.scrollHeight;
    }

    async function processCommand(cmd) {
        addTerminalLine(cmd, 'input');

        const scenarios = {
            'landing': 'Creating a premium landing page concept...',
            'design': 'Refining UI theme for high-impact visual experience...',
            'code': 'Generating optimized logic for agentic workflow...',
            'default': `Analyzing your vibe: "${cmd}"...`
        };

        let key = 'default';
        if (cmd.includes('ランディング') || cmd.includes('ページ')) key = 'landing';
        if (cmd.includes('デザイン') || cmd.includes('かっこいい')) key = 'design';
        if (cmd.includes('コード') || cmd.includes('実装')) key = 'code';

        addTerminalLine(scenarios[key]);
        previewDisplay.innerHTML = `<div class="ai-status">Thinking: ${scenarios[key]}</div>`;

        await new Promise(r => setTimeout(r, 1500));
        addTerminalLine('Step 1: Planning implementation structure...');

        await new Promise(r => setTimeout(r, 1000));
        addTerminalLine('Step 2: Executing file modifications...');

        await new Promise(r => setTimeout(r, 1000));
        addTerminalLine('Step 3: Verifying visual integrity...');

        await new Promise(r => setTimeout(r, 500));
        addTerminalLine('✅ Task Completed successfully.');

        // Update preview based on scenario
        updatePreview(key);
    }

    function updatePreview(key) {
        let content = '';
        if (key === 'landing') {
            content = `<div style="padding:20px; border:2px solid var(--primary); border-radius:10px; animation: fadeIn 1s;">
                <h1 style="color:var(--primary)">VIBE PAGE</h1>
                <p>Premium generated concept.</p>
                <button class="btn btn-primary" style="padding:5px 15px; font-size:12px;">ACTION</button>
            </div>`;
        } else if (key === 'design') {
            content = `<div style="width:100px; height:100px; background:linear-gradient(45deg, var(--primary), var(--secondary)); border-radius:50%; filter:blur(10px); animation: pulse 2s infinite;"></div>`;
        } else if (key === 'code') {
            content = `<pre style="font-size:10px; color:#0f0;">function vibe() {\n  return "AESTHETICS";\n}</pre>`;
        } else {
            content = `<div class="placeholder-msg">Your Vibe has been received. <br> Agent is now ready for the next order.</div>`;
        }
        previewDisplay.innerHTML = content;
    }

    const styleOverride = document.createElement('style');
    styleOverride.textContent = `
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
    `;
    document.head.appendChild(styleOverride);
});
