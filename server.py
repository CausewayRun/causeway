"""FastAPI server for causeway rules."""
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3
from pathlib import Path

app = FastAPI(title="causeway", docs_url="/api/docs")
DB_PATH = Path(__file__).parent / "brain.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class RuleCreate(BaseModel):
    type: str = "regex"
    pattern: Optional[str] = None
    patterns: Optional[str] = None
    description: str
    problem: Optional[str] = None
    solution: Optional[str] = None
    tool: Optional[str] = None
    action: str = "block"
    active: int = 1
    priority: int = 0
    llm_review: int = 0
    prompt: Optional[str] = None


class RuleUpdate(BaseModel):
    type: Optional[str] = None
    pattern: Optional[str] = None
    patterns: Optional[str] = None
    description: Optional[str] = None
    problem: Optional[str] = None
    solution: Optional[str] = None
    tool: Optional[str] = None
    action: Optional[str] = None
    active: Optional[int] = None
    priority: Optional[int] = None
    llm_review: Optional[int] = None
    prompt: Optional[str] = None


@app.get("/api/rules")
def list_rules():
    conn = get_db()
    rows = conn.execute("""
        SELECT id, type, pattern, patterns, description, problem, solution,
               tool, action, active, priority, llm_review, prompt, created_at
        FROM rules
        ORDER BY active DESC, action, priority DESC, id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/rules/{rule_id}")
def get_rule(rule_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Rule not found")
    return dict(row)


@app.post("/api/rules")
def create_rule(rule: RuleCreate):
    conn = get_db()
    cursor = conn.execute("""
        INSERT INTO rules (type, pattern, patterns, description, problem, solution, tool, action, active, priority, llm_review, prompt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (rule.type, rule.pattern, rule.patterns, rule.description, rule.problem, rule.solution,
          rule.tool, rule.action, rule.active, rule.priority, rule.llm_review, rule.prompt))
    conn.commit()
    rule_id = cursor.lastrowid
    conn.close()
    return {"id": rule_id}


@app.put("/api/rules/{rule_id}")
def update_rule(rule_id: int, rule: RuleUpdate):
    conn = get_db()
    existing = conn.execute("SELECT id FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Rule not found")

    fields = ["type", "pattern", "patterns", "description", "problem", "solution", "tool", "action", "active", "priority", "llm_review", "prompt"]
    updates = []
    values = []
    for field in fields:
        val = getattr(rule, field)
        if val is not None:
            updates.append(f"{field} = ?")
            values.append(val)

    if updates:
        values.append(rule_id)
        conn.execute(f"UPDATE rules SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int):
    conn = get_db()
    conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    conn.execute("DELETE FROM rule_embeddings WHERE rule_id = ?", (rule_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int):
    conn = get_db()
    row = conn.execute("SELECT active FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Rule not found")
    new_active = 0 if row["active"] else 1
    conn.execute("UPDATE rules SET active = ? WHERE id = ?", (new_active, rule_id))
    conn.commit()
    conn.close()
    return {"active": new_active}


@app.get("/api/rules/{rule_id}/history")
def get_rule_history(rule_id: int):
    """Get the source session and messages that created/triggered this rule."""
    conn = get_db()

    # Get rule with source info
    rule = conn.execute("""
        SELECT r.*, m.session_id as source_session_id, m.content as source_message,
               s.task as session_task, s.started_at as session_started,
               p.name as project_name, p.path as project_path
        FROM rules r
        LEFT JOIN messages m ON r.source_message_id = m.id
        LEFT JOIN sessions s ON m.session_id = s.id
        LEFT JOIN projects p ON s.project_id = p.id
        WHERE r.id = ?
    """, (rule_id,)).fetchone()

    if not rule:
        conn.close()
        raise HTTPException(status_code=404, detail="Rule not found")

    result = dict(rule)

    # Get triggers (when this rule blocked/warned)
    triggers = conn.execute("""
        SELECT rt.*, tc.tool, tc.input, tc.timestamp as trigger_time,
               s.task as session_task, p.name as project_name
        FROM rule_triggers rt
        JOIN tool_calls tc ON rt.tool_call_id = tc.id
        JOIN messages m ON tc.message_id = m.id
        JOIN sessions s ON m.session_id = s.id
        JOIN projects p ON s.project_id = p.id
        WHERE rt.rule_id = ?
        ORDER BY rt.timestamp DESC
        LIMIT 20
    """, (rule_id,)).fetchall()

    result['triggers'] = [dict(t) for t in triggers]

    # If we have a source session, get its messages
    if rule['source_session_id']:
        messages = conn.execute("""
            SELECT id, role, content, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp
            LIMIT 50
        """, (rule['source_session_id'],)).fetchall()
        result['source_session_messages'] = [dict(m) for m in messages]

    conn.close()
    return result


@app.get("/api/sessions")
def list_sessions():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.id, s.task, s.status, s.started_at, s.ended_at,
               p.name as project_name, p.path as project_path,
               (SELECT COUNT(*) FROM messages WHERE session_id = s.id) as message_count,
               (SELECT COUNT(*) FROM rules WHERE source_message_id IN
                   (SELECT id FROM messages WHERE session_id = s.id)) as rules_created
        FROM sessions s
        JOIN projects p ON s.project_id = p.id
        ORDER BY s.started_at DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int):
    conn = get_db()
    session = conn.execute("""
        SELECT s.*, p.name as project_name, p.path as project_path
        FROM sessions s
        JOIN projects p ON s.project_id = p.id
        WHERE s.id = ?
    """, (session_id,)).fetchone()

    if not session:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")

    messages = conn.execute("""
        SELECT id, role, content, timestamp FROM messages
        WHERE session_id = ? ORDER BY timestamp
    """, (session_id,)).fetchall()

    conn.close()
    return {"session": dict(session), "messages": [dict(m) for m in messages]}


@app.get("/api/stats")
def get_stats():
    conn = get_db()
    stats = {
        'total': conn.execute("SELECT COUNT(*) as c FROM rules").fetchone()['c'],
        'active': conn.execute("SELECT COUNT(*) as c FROM rules WHERE active = 1").fetchone()['c'],
        'block': conn.execute("SELECT COUNT(*) as c FROM rules WHERE action = 'block' AND active = 1").fetchone()['c'],
        'warn': conn.execute("SELECT COUNT(*) as c FROM rules WHERE action = 'warn' AND active = 1").fetchone()['c'],
        'llm_review': conn.execute("SELECT COUNT(*) as c FROM rules WHERE llm_review = 1 AND active = 1").fetchone()['c'],
    }
    conn.close()
    return stats


HTML = '''<!DOCTYPE html>
<html>
<head>
    <title>causeway</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #111; color: #eee; font: 13px/1.5 'SF Mono', Monaco, monospace; padding: 20px; }
        h1 { font-size: 16px; font-weight: 600; margin-bottom: 16px; color: #888; }
        .stats { display: flex; gap: 24px; margin-bottom: 24px; padding: 12px 16px; background: #1a1a1a; border-radius: 6px; }
        .stat { text-align: center; }
        .stat-val { font-size: 20px; font-weight: 700; color: #fff; }
        .stat-lbl { font-size: 10px; color: #666; text-transform: uppercase; }

        .toolbar { display: flex; gap: 12px; margin-bottom: 16px; align-items: center; }
        input, select, textarea { background: #222; border: 1px solid #333; color: #eee; padding: 6px 10px; border-radius: 4px; font: inherit; }
        input:focus, select:focus, textarea:focus { outline: none; border-color: #666; }
        .search { flex: 1; }

        .btn { background: #333; color: #eee; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font: inherit; }
        .btn:hover { background: #444; }
        .btn-primary { background: #4f46e5; }
        .btn-primary:hover { background: #4338ca; }
        .btn-danger { color: #f87171; }
        .btn-sm { padding: 2px 8px; font-size: 11px; }

        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 8px 12px; background: #1a1a1a; color: #888; font-size: 10px; text-transform: uppercase; border-bottom: 1px solid #333; }
        td { padding: 10px 12px; border-bottom: 1px solid #222; vertical-align: top; }
        tr:hover { background: #1a1a1a; }
        tr.disabled { opacity: 0.4; }

        .tag { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; }
        .tag-block { background: #7f1d1d; color: #fca5a5; }
        .tag-warn { background: #713f12; color: #fde047; }
        .tag-log { background: #14532d; color: #86efac; }
        .tag-regex { background: #1e3a5f; color: #93c5fd; }
        .tag-semantic { background: #3f3f46; color: #a1a1aa; }
        .tag-llm { background: #4c1d95; color: #c4b5fd; }

        .pattern { color: #f472b6; font-size: 12px; }
        .prompt { color: #67e8f9; font-size: 11px; font-style: italic; margin-top: 4px; }
        .meta { color: #666; font-size: 11px; margin-top: 2px; }

        tr.clickable { cursor: pointer; }
        tr.expanded { background: #1a1a1a; }
        .detail-row td { padding: 0; border-bottom: 1px solid #333; background: #0d0d0d; }
        .detail-content { padding: 16px 12px 16px 60px; }
        .detail-section { margin-bottom: 12px; }
        .detail-section:last-child { margin-bottom: 0; }
        .detail-label { font-size: 10px; color: #666; text-transform: uppercase; margin-bottom: 4px; }
        .detail-value { color: #aaa; }
        .detail-value a { color: #93c5fd; text-decoration: none; }
        .detail-value a:hover { text-decoration: underline; }
        .trigger-list { margin-top: 8px; }
        .trigger-item { padding: 6px 10px; background: #1a1a1a; border-radius: 4px; margin-bottom: 4px; font-size: 11px; }
        .trigger-tool { color: #f472b6; }
        .trigger-time { color: #666; float: right; }

        .modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,0.8); display: none; align-items: center; justify-content: center; }
        .modal-bg.open { display: flex; }
        .modal { background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 20px; width: 500px; max-width: 90%; }
        .modal h2 { font-size: 14px; margin-bottom: 16px; color: #888; }
        .form-row { margin-bottom: 12px; }
        .form-row label { display: block; font-size: 10px; color: #666; text-transform: uppercase; margin-bottom: 4px; }
        .form-row input, .form-row select, .form-row textarea { width: 100%; }
        .form-row textarea { min-height: 60px; resize: vertical; }
        .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .form-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
        .checkbox { display: flex; align-items: center; gap: 8px; }
        .checkbox input { width: auto; }
    </style>
</head>
<body>
    <h1>causeway / rules</h1>

    <div class="stats" id="stats"></div>

    <div class="toolbar">
        <input type="text" class="search" placeholder="filter rules..." id="search" onkeyup="filter()">
        <select id="filter-action" onchange="filter()">
            <option value="">all actions</option>
            <option value="block">block</option>
            <option value="warn">warn</option>
            <option value="log">log</option>
        </select>
        <select id="filter-type" onchange="filter()">
            <option value="">all types</option>
            <option value="regex">regex</option>
            <option value="semantic">semantic</option>
        </select>
        <button class="btn btn-primary" onclick="openModal()">+ new rule</button>
    </div>

    <table>
        <thead>
            <tr>
                <th style="width:50px">id</th>
                <th style="width:80px">action</th>
                <th style="width:80px">type</th>
                <th>description / patterns</th>
                <th style="width:80px">tool</th>
                <th style="width:120px"></th>
            </tr>
        </thead>
        <tbody id="rules"></tbody>
    </table>

    <div class="modal-bg" id="modal">
        <div class="modal">
            <h2 id="modal-title">new rule</h2>
            <form id="form" onsubmit="save(event)">
                <input type="hidden" id="f-id">
                <div class="form-grid">
                    <div class="form-row">
                        <label>type</label>
                        <select id="f-type">
                            <option value="regex">regex</option>
                            <option value="semantic">semantic</option>
                        </select>
                    </div>
                    <div class="form-row">
                        <label>action</label>
                        <select id="f-action">
                            <option value="block">block</option>
                            <option value="warn">warn</option>
                            <option value="log">log</option>
                        </select>
                    </div>
                </div>
                <div class="form-row">
                    <label>description</label>
                    <input type="text" id="f-description" required placeholder="what this rule does">
                </div>
                <div class="form-row">
                    <label>pattern (single regex)</label>
                    <input type="text" id="f-pattern" placeholder="^rm -rf">
                </div>
                <div class="form-row">
                    <label>patterns (JSON array)</label>
                    <input type="text" id="f-patterns" placeholder='["main\\.py", "config\\.py"]'>
                </div>
                <div class="form-grid">
                    <div class="form-row">
                        <label>tool (blank = all)</label>
                        <select id="f-tool">
                            <option value="">all tools</option>
                            <option value="Bash">Bash</option>
                            <option value="Write">Write</option>
                            <option value="Edit">Edit</option>
                            <option value="Read">Read</option>
                        </select>
                    </div>
                    <div class="form-row">
                        <label>priority</label>
                        <input type="number" id="f-priority" value="0">
                    </div>
                </div>
                <div class="form-row checkbox">
                    <input type="checkbox" id="f-llm">
                    <label style="margin:0">llm_review (LLM evaluates matches)</label>
                </div>
                <div class="form-row">
                    <label>prompt (for LLM review)</label>
                    <textarea id="f-prompt" placeholder="what should the LLM check for?"></textarea>
                </div>
                <div class="form-row">
                    <label>problem</label>
                    <textarea id="f-problem" placeholder="why is this a problem?"></textarea>
                </div>
                <div class="form-row">
                    <label>solution</label>
                    <textarea id="f-solution" placeholder="what to do instead?"></textarea>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn" onclick="closeModal()">cancel</button>
                    <button type="submit" class="btn btn-primary">save</button>
                </div>
            </form>
        </div>
    </div>

<script>
let rules = [];

async function load() {
    const [rulesData, stats] = await Promise.all([
        fetch('/api/rules').then(r => r.json()),
        fetch('/api/stats').then(r => r.json())
    ]);
    rules = rulesData;

    document.getElementById('stats').innerHTML = `
        <div class="stat"><div class="stat-val">${stats.active}</div><div class="stat-lbl">active</div></div>
        <div class="stat"><div class="stat-val">${stats.block}</div><div class="stat-lbl">block</div></div>
        <div class="stat"><div class="stat-val">${stats.warn}</div><div class="stat-lbl">warn</div></div>
        <div class="stat"><div class="stat-val">${stats.llm_review}</div><div class="stat-lbl">llm review</div></div>
        <div class="stat"><div class="stat-val">${stats.total}</div><div class="stat-lbl">total</div></div>
    `;

    filter();
}

function filter() {
    const q = document.getElementById('search').value.toLowerCase();
    const action = document.getElementById('filter-action').value;
    const type = document.getElementById('filter-type').value;

    const filtered = rules.filter(r => {
        if (action && r.action !== action) return false;
        if (type && r.type !== type) return false;
        if (q) {
            const text = [r.description, r.pattern, r.patterns, r.prompt].join(' ').toLowerCase();
            if (!text.includes(q)) return false;
        }
        return true;
    });

    render(filtered);
}

let expandedId = null;
let historyCache = {};

function render(list) {
    const html = list.map(r => `
        <tr class="clickable ${r.active ? '' : 'disabled'} ${expandedId === r.id ? 'expanded' : ''}" onclick="toggleExpand(${r.id}, event)">
            <td>#${r.id}</td>
            <td><span class="tag tag-${r.action}">${r.action}</span></td>
            <td>
                <span class="tag tag-${r.type}">${r.type}</span>
                ${r.llm_review ? '<span class="tag tag-llm">llm</span>' : ''}
            </td>
            <td>
                <div>${esc(r.description)}</div>
                ${r.pattern ? `<div class="pattern">${esc(r.pattern)}</div>` : ''}
                ${r.patterns ? `<div class="pattern">${esc(r.patterns)}</div>` : ''}
                ${r.prompt ? `<div class="prompt">${esc(r.prompt)}</div>` : ''}
                ${r.problem ? `<div class="meta">problem: ${esc(r.problem)}</div>` : ''}
            </td>
            <td>${r.tool || '<span style="color:#666">all</span>'}</td>
            <td style="text-align:right" onclick="event.stopPropagation()">
                <button class="btn btn-sm" onclick="edit(${r.id})">edit</button>
                <button class="btn btn-sm" onclick="toggle(${r.id})">${r.active ? 'off' : 'on'}</button>
                <button class="btn btn-sm btn-danger" onclick="del(${r.id})">×</button>
            </td>
        </tr>
        ${expandedId === r.id ? `<tr class="detail-row"><td colspan="6"><div class="detail-content" id="detail-${r.id}">loading...</div></td></tr>` : ''}
    `).join('');

    document.getElementById('rules').innerHTML = html || '<tr><td colspan="6" style="text-align:center;color:#666;padding:40px">no rules</td></tr>';

    if (expandedId) loadHistory(expandedId);
}

async function toggleExpand(id, event) {
    if (event.target.tagName === 'BUTTON') return;
    expandedId = expandedId === id ? null : id;
    filter();
}

async function loadHistory(id) {
    const el = document.getElementById('detail-' + id);
    if (!el) return;

    if (historyCache[id]) {
        renderHistory(el, historyCache[id]);
        return;
    }

    try {
        const data = await fetch('/api/rules/' + id + '/history').then(r => r.json());
        historyCache[id] = data;
        renderHistory(el, data);
    } catch (e) {
        el.innerHTML = '<span style="color:#666">no history available</span>';
    }
}

function renderHistory(el, data) {
    let html = '';

    // Source info
    if (data.source_session_id) {
        html += `<div class="detail-section">
            <div class="detail-label">source</div>
            <div class="detail-value">
                session #${data.source_session_id}
                ${data.project_name ? ` · ${esc(data.project_name)}` : ''}
                ${data.session_task ? `<br><span style="color:#888">${esc(data.session_task.substring(0,100))}</span>` : ''}
            </div>
        </div>`;
    } else {
        html += `<div class="detail-section">
            <div class="detail-label">source</div>
            <div class="detail-value" style="color:#666">manually created</div>
        </div>`;
    }

    // Triggers
    if (data.triggers && data.triggers.length > 0) {
        html += `<div class="detail-section">
            <div class="detail-label">recent triggers (${data.triggers.length})</div>
            <div class="trigger-list">`;
        for (const t of data.triggers.slice(0, 5)) {
            html += `<div class="trigger-item">
                <span class="trigger-tool">${esc(t.tool)}</span>
                ${t.project_name ? ` · ${esc(t.project_name)}` : ''}
                <span class="trigger-time">${t.trigger_time ? new Date(t.trigger_time).toLocaleDateString() : ''}</span>
                <div style="color:#666;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((t.input || '').substring(0, 80))}</div>
            </div>`;
        }
        html += `</div></div>`;
    } else {
        html += `<div class="detail-section">
            <div class="detail-label">triggers</div>
            <div class="detail-value" style="color:#666">never triggered</div>
        </div>`;
    }

    // Created date
    if (data.created_at) {
        html += `<div class="detail-section">
            <div class="detail-label">created</div>
            <div class="detail-value">${new Date(data.created_at).toLocaleString()}</div>
        </div>`;
    }

    el.innerHTML = html;
}

function openModal(r = null) {
    document.getElementById('modal-title').textContent = r ? 'edit rule' : 'new rule';
    document.getElementById('f-id').value = r?.id || '';
    document.getElementById('f-type').value = r?.type || 'regex';
    document.getElementById('f-action').value = r?.action || 'block';
    document.getElementById('f-description').value = r?.description || '';
    document.getElementById('f-pattern').value = r?.pattern || '';
    document.getElementById('f-patterns').value = r?.patterns || '';
    document.getElementById('f-tool').value = r?.tool || '';
    document.getElementById('f-priority').value = r?.priority || 0;
    document.getElementById('f-llm').checked = r?.llm_review || false;
    document.getElementById('f-prompt').value = r?.prompt || '';
    document.getElementById('f-problem').value = r?.problem || '';
    document.getElementById('f-solution').value = r?.solution || '';
    document.getElementById('modal').classList.add('open');
}

function closeModal() {
    document.getElementById('modal').classList.remove('open');
}

function edit(id) {
    openModal(rules.find(r => r.id === id));
}

async function save(e) {
    e.preventDefault();
    const id = document.getElementById('f-id').value;
    const data = {
        type: document.getElementById('f-type').value,
        action: document.getElementById('f-action').value,
        description: document.getElementById('f-description').value,
        pattern: document.getElementById('f-pattern').value || null,
        patterns: document.getElementById('f-patterns').value || null,
        tool: document.getElementById('f-tool').value || null,
        priority: parseInt(document.getElementById('f-priority').value) || 0,
        llm_review: document.getElementById('f-llm').checked ? 1 : 0,
        prompt: document.getElementById('f-prompt').value || null,
        problem: document.getElementById('f-problem').value || null,
        solution: document.getElementById('f-solution').value || null,
    };

    if (id) {
        await fetch(`/api/rules/${id}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
    } else {
        await fetch('/api/rules', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
    }

    closeModal();
    load();
}

async function toggle(id) {
    await fetch(`/api/rules/${id}/toggle`, { method: 'PATCH' });
    load();
}

async function del(id) {
    if (!confirm('delete rule #' + id + '?')) return;
    await fetch(`/api/rules/${id}`, { method: 'DELETE' });
    load();
}

function esc(s) { return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') : ''; }

load();
</script>
</body>
</html>'''


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
