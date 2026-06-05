/**
 * AlertEditor.js - 告警规则编辑器
 * 支持可视化创建和管理告警规则
 */

const ALERT_TYPES = [
  { id: 'price', label: '价格突破', icon: '💰', fields: ['symbol', 'direction', 'threshold'] },
  { id: 'indicator', label: '指标触发', icon: '📊', fields: ['symbol', 'indicator', 'operator', 'threshold'] },
  { id: 'signal', label: '信号触发', icon: '📡', fields: ['signal_type', 'min_confidence'] },
  { id: 'change', label: '涨跌幅', icon: '📈', fields: ['symbol', 'direction', 'percent'] },
];

const INDICATORS = [
  { id: 'rsi', label: 'RSI' },
  { id: 'macd', label: 'MACD' },
  { id: 'kdj', label: 'KDJ' },
  { id: 'volume_ratio', label: '量比' },
  { id: 'adx', label: 'ADX' },
];

const OPERATORS = [
  { id: 'gt', label: '>' },
  { id: 'lt', label: '<' },
  { id: 'gte', label: '>=' },
  { id: 'lte', label: '<=' },
  { id: 'eq', label: '=' },
];

export class AlertEditor {
  constructor(container, options = {}) {
    this.container = container;
    this.rules = [];
    this.onSave = options.onSave || null;
    this.onDelete = options.onDelete || null;

    this._loadRules();
    this.render();
  }

  async _loadRules() {
    try {
      const response = await fetch('/api/alerts/rules');
      if (response.ok) {
        const data = await response.json();
        this.rules = data.rules || [];
      }
    } catch (e) {
      console.error('[AlertEditor] Failed to load rules:', e);
    }
  }

  render() {
    this.container.innerHTML = '';

    // 标题
    const header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:16px';
    header.innerHTML = '<h3 style="margin:0;font-size:14px">告警规则</h3>';
    this.container.appendChild(header);

    // 添加按钮
    const addBtn = document.createElement('button');
    addBtn.textContent = '+ 新建规则';
    addBtn.style.cssText = 'padding:6px 12px;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px';
    addBtn.addEventListener('click', () => this._showEditor());
    header.appendChild(addBtn);

    // 规则列表
    const list = document.createElement('div');
    list.className = 'alert-rules-list';

    if (this.rules.length === 0) {
      const empty = document.createElement('div');
      empty.style.cssText = 'text-align:center;padding:40px;color:var(--text-dim);font-size:13px';
      empty.textContent = '暂无告警规则，点击"新建规则"开始创建';
      list.appendChild(empty);
    } else {
      this.rules.forEach(rule => {
        list.appendChild(this._renderRule(rule));
      });
    }

    this.container.appendChild(list);
  }

  _renderRule(rule) {
    const div = document.createElement('div');
    div.className = 'alert-rule';
    div.style.cssText = 'display:flex;align-items:center;gap:12px;padding:12px;background:var(--card);border:1px solid var(--border);border-radius:6px;margin-bottom:8px';

    // 图标
    const type = ALERT_TYPES.find(t => t.id === rule.type);
    const icon = document.createElement('span');
    icon.textContent = type?.icon || '⚠️';
    icon.style.fontSize = '20px';
    div.appendChild(icon);

    // 描述
    const desc = document.createElement('div');
    desc.style.cssText = 'flex:1;min-width:0';
    desc.innerHTML = `
      <div style="font-weight:500;font-size:13px">${this._getRuleDescription(rule)}</div>
      <div style="font-size:11px;color:var(--text-dim);margin-top:2px">${rule.enabled ? '已启用' : '已禁用'}</div>
    `;
    div.appendChild(desc);

    // 启用/禁用开关
    const toggle = document.createElement('button');
    toggle.textContent = rule.enabled ? '禁用' : '启用';
    toggle.style.cssText = `padding:4px 8px;border:1px solid var(--border);border-radius:4px;background:${rule.enabled ? 'var(--card)' : 'var(--accent)'};color:${rule.enabled ? 'var(--text)' : '#fff'};cursor:pointer;font-size:11px`;
    toggle.addEventListener('click', () => this._toggleRule(rule.id));
    div.appendChild(toggle);

    // 删除按钮
    const deleteBtn = document.createElement('button');
    deleteBtn.textContent = '删除';
    deleteBtn.style.cssText = 'padding:4px 8px;border:1px solid var(--down);border-radius:4px;background:none;color:var(--down);cursor:pointer;font-size:11px';
    deleteBtn.addEventListener('click', () => this._deleteRule(rule.id));
    div.appendChild(deleteBtn);

    return div;
  }

  _getRuleDescription(rule) {
    const type = ALERT_TYPES.find(t => t.id === rule.type);
    const typeLabel = type?.label || rule.type;

    switch (rule.type) {
      case 'price':
        return `${rule.symbol} 价格${rule.direction === 'up' ? '上穿' : '下穿'} ${rule.threshold}`;
      case 'indicator':
        const ind = INDICATORS.find(i => i.id === rule.indicator);
        return `${rule.symbol} ${ind?.label || rule.indicator} ${rule.operator} ${rule.threshold}`;
      case 'signal':
        return `${rule.signal_type} 信号置信度 > ${rule.min_confidence}%`;
      case 'change':
        return `${rule.symbol} ${rule.direction === 'up' ? '涨幅' : '跌幅'} > ${rule.percent}%`;
      default:
        return `${typeLabel} 规则`;
    }
  }

  _showEditor(rule = null) {
    // 创建编辑器模态框
    const modal = document.createElement('div');
    modal.className = 'alert-editor-modal';
    modal.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:1000';

    const content = document.createElement('div');
    content.style.cssText = 'background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:20px;width:400px;max-height:80vh;overflow-y:auto';

    // 标题
    const title = document.createElement('h3');
    title.textContent = rule ? '编辑规则' : '新建规则';
    title.style.cssText = 'margin:0 0 16px;font-size:16px';
    content.appendChild(title);

    // 规则类型选择
    const typeGroup = document.createElement('div');
    typeGroup.style.cssText = 'margin-bottom:16px';
    typeGroup.innerHTML = '<label style="display:block;font-size:12px;color:var(--text-dim);margin-bottom:4px">规则类型</label>';

    const typeSelect = document.createElement('select');
    typeSelect.style.cssText = 'width:100%;padding:8px;border:1px solid var(--border);border-radius:4px;background:var(--card);color:var(--text)';
    ALERT_TYPES.forEach(t => {
      const option = document.createElement('option');
      option.value = t.id;
      option.textContent = `${t.icon} ${t.label}`;
      if (rule?.type === t.id) option.selected = true;
      typeSelect.appendChild(option);
    });
    typeGroup.appendChild(typeSelect);
    content.appendChild(typeGroup);

    // 动态字段容器
    const fieldsContainer = document.createElement('div');
    fieldsContainer.id = 'alertFields';
    content.appendChild(fieldsContainer);

    // 根据类型渲染字段
    const renderFields = (type) => {
      fieldsContainer.innerHTML = '';
      const typeConfig = ALERT_TYPES.find(t => t.id === type);
      if (!typeConfig) return;

      typeConfig.fields.forEach(field => {
        const group = document.createElement('div');
        group.style.cssText = 'margin-bottom:12px';

        const label = document.createElement('label');
        label.style.cssText = 'display:block;font-size:12px;color:var(--text-dim);margin-bottom:4px';

        let input;

        switch (field) {
          case 'symbol':
            label.textContent = '股票代码';
            input = document.createElement('input');
            input.type = 'text';
            input.placeholder = '000001';
            input.value = rule?.symbol || '';
            break;

          case 'direction':
            label.textContent = '方向';
            input = document.createElement('select');
            [{ id: 'up', label: '上涨/上穿' }, { id: 'down', label: '下跌/下穿' }].forEach(d => {
              const opt = document.createElement('option');
              opt.value = d.id;
              opt.textContent = d.label;
              if (rule?.direction === d.id) opt.selected = true;
              input.appendChild(opt);
            });
            break;

          case 'threshold':
          case 'percent':
            label.textContent = field === 'threshold' ? '阈值' : '百分比';
            input = document.createElement('input');
            input.type = 'number';
            input.step = '0.01';
            input.value = rule?.[field] || '';
            break;

          case 'indicator':
            label.textContent = '指标';
            input = document.createElement('select');
            INDICATORS.forEach(ind => {
              const opt = document.createElement('option');
              opt.value = ind.id;
              opt.textContent = ind.label;
              if (rule?.indicator === ind.id) opt.selected = true;
              input.appendChild(opt);
            });
            break;

          case 'operator':
            label.textContent = '条件';
            input = document.createElement('select');
            OPERATORS.forEach(op => {
              const opt = document.createElement('option');
              opt.value = op.id;
              opt.textContent = op.label;
              if (rule?.operator === op.id) opt.selected = true;
              input.appendChild(opt);
            });
            break;

          case 'signal_type':
            label.textContent = '信号类型';
            input = document.createElement('select');
            ['买入', '卖出', '加仓', '减仓'].forEach(s => {
              const opt = document.createElement('option');
              opt.value = s;
              opt.textContent = s;
              if (rule?.signal_type === s) opt.selected = true;
              input.appendChild(opt);
            });
            break;

          case 'min_confidence':
            label.textContent = '最低置信度 (%)';
            input = document.createElement('input');
            input.type = 'number';
            input.min = '0';
            input.max = '100';
            input.value = rule?.min_confidence || '70';
            break;
        }

        if (input) {
          input.id = `alert_${field}`;
          input.style.cssText = 'width:100%;padding:8px;border:1px solid var(--border);border-radius:4px;background:var(--card);color:var(--text)';
          group.appendChild(label);
          group.appendChild(input);
          fieldsContainer.appendChild(group);
        }
      });
    };

    typeSelect.addEventListener('change', () => renderFields(typeSelect.value));
    renderFields(rule?.type || 'price');

    // 按钮组
    const buttons = document.createElement('div');
    buttons.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:20px';

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = '取消';
    cancelBtn.style.cssText = 'padding:8px 16px;border:1px solid var(--border);border-radius:4px;background:var(--card);color:var(--text);cursor:pointer';
    cancelBtn.addEventListener('click', () => modal.remove());
    buttons.appendChild(cancelBtn);

    const saveBtn = document.createElement('button');
    saveBtn.textContent = '保存';
    saveBtn.style.cssText = 'padding:8px 16px;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer';
    saveBtn.addEventListener('click', () => {
      const ruleData = this._collectFormData(typeSelect.value);
      if (rule) {
        ruleData.id = rule.id;
      }
      this._saveRule(ruleData);
      modal.remove();
    });
    buttons.appendChild(saveBtn);

    content.appendChild(buttons);
    modal.appendChild(content);

    // 点击背景关闭
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });

    document.body.appendChild(modal);
  }

  _collectFormData(type) {
    const data = { type, enabled: true };

    ALERT_TYPES.find(t => t.id === type)?.fields.forEach(field => {
      const el = document.getElementById(`alert_${field}`);
      if (el) {
        data[field] = el.type === 'number' ? parseFloat(el.value) : el.value;
      }
    });

    return data;
  }

  async _saveRule(ruleData) {
    try {
      const response = await fetch('/api/alerts/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ruleData)
      });

      if (response.ok) {
        await this._loadRules();
        this.render();
        if (this.onSave) this.onSave(ruleData);
      }
    } catch (e) {
      console.error('[AlertEditor] Failed to save rule:', e);
    }
  }

  async _toggleRule(ruleId) {
    const rule = this.rules.find(r => r.id === ruleId);
    if (!rule) return;

    rule.enabled = !rule.enabled;

    try {
      await fetch(`/api/alerts/rules/${ruleId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: rule.enabled })
      });
      this.render();
    } catch (e) {
      console.error('[AlertEditor] Failed to toggle rule:', e);
    }
  }

  async _deleteRule(ruleId) {
    if (!confirm('确定删除此规则？')) return;

    try {
      const response = await fetch(`/api/alerts/rules/${ruleId}`, {
        method: 'DELETE'
      });

      if (response.ok) {
        await this._loadRules();
        this.render();
        if (this.onDelete) this.onDelete(ruleId);
      }
    } catch (e) {
      console.error('[AlertEditor] Failed to delete rule:', e);
    }
  }
}
