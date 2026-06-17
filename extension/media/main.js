(function () {
  const vscode = acquireVsCodeApi();
  const log = document.getElementById('log');
  const promptEl = document.getElementById('prompt');
  const sendBtn = document.getElementById('send');

  let assistantEl = null; // current streaming assistant bubble
  let busy = false;

  function scroll() {
    log.scrollTop = log.scrollHeight;
  }

  function add(cls, html) {
    const el = document.createElement('div');
    el.className = 'msg ' + cls;
    el.innerHTML = html;
    log.appendChild(el);
    scroll();
    return el;
  }

  function escapeHtml(s) {
    return (s || '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  }

  function renderDiff(patch) {
    return patch
      .split('\n')
      .map((line) => {
        let cls = 'd-ctx';
        if (line.startsWith('+') && !line.startsWith('+++')) cls = 'd-add';
        else if (line.startsWith('-') && !line.startsWith('---')) cls = 'd-del';
        else if (line.startsWith('@@')) cls = 'd-hunk';
        return '<span class="' + cls + '">' + escapeHtml(line) + '</span>';
      })
      .join('\n');
  }

  function setBusy(b) {
    busy = b;
    sendBtn.disabled = b;
    sendBtn.textContent = b ? '…' : 'Run';
  }

  function run() {
    const text = promptEl.value.trim();
    if (!text || busy) return;
    add('user', escapeHtml(text));
    promptEl.value = '';
    assistantEl = null;
    setBusy(true);
    vscode.postMessage({ command: 'run', text });
  }

  sendBtn.addEventListener('click', run);
  promptEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      run();
    }
  });

  function approvalCard(ev) {
    const wrap = document.createElement('div');
    wrap.className = 'msg approval';
    const head = document.createElement('div');
    head.className = 'approval-head';
    head.textContent = 'Approve: ' + ev.name;
    wrap.appendChild(head);

    if (ev.preview) {
      const pre = document.createElement('pre');
      pre.className = 'diff';
      pre.innerHTML = renderDiff(ev.preview);
      wrap.appendChild(pre);
    }

    const fb = document.createElement('input');
    fb.type = 'text';
    fb.placeholder = 'Optional feedback (sent if you reject)';
    fb.className = 'approval-feedback';
    wrap.appendChild(fb);

    const row = document.createElement('div');
    row.className = 'approval-actions';
    const yes = document.createElement('button');
    yes.textContent = 'Approve';
    yes.className = 'approve';
    const no = document.createElement('button');
    no.textContent = 'Reject';
    no.className = 'reject';
    const finish = (approved) => {
      vscode.postMessage({
        command: 'approval',
        id: ev.id,
        approved,
        feedback: fb.value || undefined
      });
      yes.disabled = no.disabled = true;
      head.textContent = (approved ? '✓ Approved: ' : '✗ Rejected: ') + ev.name;
    };
    yes.addEventListener('click', () => finish(true));
    no.addEventListener('click', () => finish(false));
    row.appendChild(yes);
    row.appendChild(no);
    wrap.appendChild(row);
    log.appendChild(wrap);
    scroll();
  }

  window.addEventListener('message', (event) => {
    const ev = event.data;
    switch (ev.type) {
      case 'status':
        add('status', escapeHtml(ev.message));
        break;
      case 'token':
        if (!assistantEl) assistantEl = add('assistant', '');
        assistantEl.textContent += ev.text;
        scroll();
        break;
      case 'assistant_message':
        // streaming already rendered the text; just close this bubble
        assistantEl = null;
        break;
      case 'tool_start': {
        const a = ev.args || {};
        const detail = a.path || a.command || a.query || '';
        add('tool', '⚙ <b>' + escapeHtml(ev.name) + '</b> ' + escapeHtml(detail));
        break;
      }
      case 'diff':
        add('diffwrap', '<div class="diff-path">' + escapeHtml(ev.path) +
          (ev.is_new ? ' (new)' : '') + '</div><pre class="diff">' +
          renderDiff(ev.patch) + '</pre>');
        break;
      case 'tool_result': {
        const mark = ev.ok ? '✓' : '✗';
        const out = (ev.output || '').slice(0, 1500);
        add('result ' + (ev.ok ? 'ok' : 'fail'),
          mark + ' <pre>' + escapeHtml(out) + '</pre>');
        break;
      }
      case 'approval_request':
        approvalCard(ev);
        break;
      case 'error':
        add('error', '⚠ ' + escapeHtml(ev.message));
        break;
      case 'done':
        setBusy(false);
        assistantEl = null;
        break;
    }
  });

  vscode.postMessage({ command: 'ready' });
})();
