document.addEventListener('DOMContentLoaded', () => {
  const storageKey = 'codex-master:labs';
  const state = JSON.parse(localStorage.getItem(storageKey) || '{"labs":{},"quizzes":{}}');
  const checks = [...document.querySelectorAll('.check input[type="checkbox"]')];
  const quizzes = [...document.querySelectorAll('.quiz')];

  function save() {
    localStorage.setItem(storageKey, JSON.stringify(state));
  }

  function updateMastery() {
    const checkDone = checks.filter(check => check.checked).length;
    const labDone = ['prompt', 'delegation'].filter(key => state.labs[key]).length;
    const quizDone = quizzes.filter((quiz, index) => state.quizzes[index]).length;
    const total = checks.length + 2 + quizzes.length;
    const done = checkDone + labDone + quizDone;
    const pct = total ? Math.round(done / total * 100) : 0;
    const ranks = pct >= 100 ? 'Master' : pct >= 75 ? 'Architect' : pct >= 50 ? 'Operator' : pct >= 25 ? 'Apprentice' : 'Observer';

    document.querySelector('.progress-fill').style.width = pct + '%';
    document.querySelector('.progress-value').textContent = pct + '%';
    document.getElementById('mastery-rank').textContent = ranks;
    document.getElementById('master-meter-fill').style.width = pct + '%';
    document.getElementById('master-status').textContent =
      pct >= 100 ? 'Master認定: 委任・権限・検証・復旧を一つの運用系として説明できます。' :
      done + ' / ' + total + ' 完了。次の未完了項目へ進んでください。';
  }

  checks.forEach(check => check.addEventListener('change', updateMastery));

  const promptInput = document.getElementById('prompt-lab');
  const promptResult = document.getElementById('prompt-result');
  document.getElementById('analyze-prompt').addEventListener('click', () => {
    const value = promptInput.value.trim();
    const criteria = [
      ['Goal', /目的|目標|実装|修正|作成|変更|解消|goal/i],
      ['Context', /背景|現在|再現|関連|症状|エラー|error|src\/|file|context/i],
      ['Constraints', /制約|維持|禁止|対象外|変更しない|追加しない|必ず|constraint/i],
      ['Done', /完了|成功条件|done|通る|解消する|表示される|動作する/i],
      ['Evidence', /test|テスト|lint|build|検証|確認|review|差分|証拠/i]
    ];
    const hits = criteria.map(([name, pattern]) => ({ name, ok: pattern.test(value) }));
    const score = hits.filter(item => item.ok).length * 20;
    const missing = hits.filter(item => !item.ok).map(item => item.name);
    promptResult.innerHTML =
      '<span class="score">' + score + '/100</span>' +
      hits.map(item => (item.ok ? 'OK ' : '不足 ') + item.name).join(' / ') +
      (missing.length ? '<br><br>追記候補: ' + missing.join('、') : '<br><br>構造上の要素は揃っています。対象repoで事実を確認してください。');
    if (value.length >= 40 && score >= 60) {
      state.labs.prompt = true;
      save();
      updateMastery();
    }
  });

  document.getElementById('select-delegation').addEventListener('click', () => {
    const type = document.getElementById('task-type').value;
    const risk = document.getElementById('task-risk').value;
    const data = document.getElementById('task-data').value;
    const repeat = document.getElementById('task-repeat').value;
    let surface = 'CLIまたはCodex AppのLocal thread';
    let permission = risk === 'readonly' ? 'Read-only' : risk === 'high' ? 'Workspace-write + on-request。重要判断で停止' : 'Workspace-write + on-request';
    let durable = 'Prompt / Thread';

    if (type === 'research') surface = 'ChatまたはLocal Read-only';
    if (type === 'parallel') surface = 'Codex AppのWorktreeまたはCloud';
    if (type === 'routine') surface = 'まず対話実行。安定後にcodex exec';
    if (data === 'yes') surface += ' + MCP / App connector';
    if (repeat === 'often') durable = 'AGENTS.mdまたはSkill';
    if (repeat === 'scheduled') durable = 'Skillで安定化後、Automation';
    if (risk === 'high' && type === 'parallel') surface += '。編集所有を分離';

    document.getElementById('delegation-result').innerHTML =
      '<b>Surface:</b> ' + surface + '<br><b>Permission:</b> ' + permission + '<br><b>Durable layer:</b> ' + durable +
      '<br><br>実行前に完了条件、停止条件、検証証拠を定義してください。';
    state.labs.delegation = true;
    save();
    updateMastery();
  });

  quizzes.forEach((quiz, index) => {
    const answer = quiz.dataset.answer;
    const feedback = quiz.querySelector('.quiz-feedback');
    const restore = state.quizzes[index];
    if (restore) {
      quiz.querySelector('[data-choice="' + answer + '"]').classList.add('correct');
      feedback.textContent = '正解済み';
    }

    quiz.querySelectorAll('button[data-choice]').forEach(button => {
      button.addEventListener('click', () => {
        quiz.querySelectorAll('button').forEach(item => item.classList.remove('correct', 'wrong'));
        if (button.dataset.choice === answer) {
          button.classList.add('correct');
          feedback.textContent = '正解。判断の根拠を自分の言葉でも説明してください。';
          state.quizzes[index] = true;
          save();
          updateMastery();
        } else {
          button.classList.add('wrong');
          feedback.textContent = '再考してください。速さより、所有境界・新しい証拠・手順の安定性を優先します。';
        }
      });
    });
  });

  updateMastery();
});
