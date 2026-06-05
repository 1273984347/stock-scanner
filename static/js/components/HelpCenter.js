/**
 * HelpCenter.js - 帮助中心组件
 * 提供 FAQ、使用指南、快捷键速查
 */

export class HelpCenter {
  constructor() {
    this._isVisible = false;
    this._currentTab = 'faq';
  }

  show() {
    if (this._isVisible) return;
    this._isVisible = true;

    const modal = document.createElement('div');
    modal.id = 'helpCenterModal';
    modal.className = 'modal-overlay active';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', '帮助中心');

    modal.innerHTML = `
      <div class="modal" style="max-width:700px;max-height:80vh;overflow-y:auto">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <h3 style="margin:0">帮助中心</h3>
          <button class="modal-close" aria-label="关闭">&times;</button>
        </div>

        <div style="display:flex;gap:8px;margin-bottom:16px;border-bottom:1px solid var(--border);padding-bottom:8px">
          <button class="help-tab active" data-tab="faq">FAQ</button>
          <button class="help-tab" data-tab="guide">使用指南</button>
          <button class="help-tab" data-tab="shortcuts">快捷键</button>
        </div>

        <div id="helpContent"></div>
      </div>
    `;

    document.body.appendChild(modal);

    // 绑定事件
    modal.querySelector('.modal-close').addEventListener('click', () => this.hide());
    modal.addEventListener('click', (e) => {
      if (e.target === modal) this.hide();
    });

    modal.querySelectorAll('.help-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        this._currentTab = tab.dataset.tab;
        modal.querySelectorAll('.help-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        this._renderContent();
      });
    });

    // 快捷键关闭
    const handleEsc = (e) => {
      if (e.key === 'Escape') {
        this.hide();
        document.removeEventListener('keydown', handleEsc);
      }
    };
    document.addEventListener('keydown', handleEsc);

    this._renderContent();
  }

  hide() {
    const modal = document.getElementById('helpCenterModal');
    if (modal) {
      modal.remove();
    }
    this._isVisible = false;
  }

  _renderContent() {
    const container = document.getElementById('helpContent');
    if (!container) return;

    switch (this._currentTab) {
      case 'faq':
        container.innerHTML = this._renderFAQ();
        break;
      case 'guide':
        container.innerHTML = this._renderGuide();
        break;
      case 'shortcuts':
        container.innerHTML = this._renderShortcuts();
        break;
    }
  }

  _renderFAQ() {
    const faqs = [
      {
        q: '如何开始使用量化交易系统？',
        a: '点击右侧面板的"刷新数据"按钮，系统会自动扫描市场并生成交易信号。首次使用建议选择"均衡策略"。'
      },
      {
        q: '如何查看股票详情？',
        a: '在选股结果或持仓列表中点击股票代码，会弹出详情窗口显示评分、因子、信号等信息。也可以点击"新窗口"按钮在独立窗口查看。'
      },
      {
        q: '如何切换主题？',
        a: '点击工具栏右侧的主题图标，可以选择亮色、暗色或高对比度主题。'
      },
      {
        q: '如何调整字体大小？',
        a: '进入"设置"面板，在"字体大小"下拉菜单中选择 12px-20px 的字号。'
      },
      {
        q: '如何导出数据？',
        a: '在持仓或交易历史页面，点击"导出 CSV"或"导出 Excel"按钮。图表右上角的相机图标可以导出图片。'
      },
      {
        q: '如何设置告警？',
        a: '在设置面板的"告警规则"部分，可以创建价格、指标、信号等类型的告警规则。'
      },
      {
        q: '回测结果不理想怎么办？',
        a: '尝试调整止损止盈参数、更换策略预设（激进/保守），或使用"策略对比"功能比较不同参数的效果。'
      },
      {
        q: '如何查看因子评价？',
        a: '切换到"因子评估"标签页，可以看到各因子的 IC 值、相关性、权重分布等信息。'
      }
    ];

    return faqs.map(faq => `
      <div style="margin-bottom:16px;padding:12px;background:var(--card);border:1px solid var(--border);border-radius:6px">
        <div style="font-weight:600;margin-bottom:8px;color:var(--accent)">${faq.q}</div>
        <div style="font-size:13px;color:var(--text-sub);line-height:1.6">${faq.a}</div>
      </div>
    `).join('');
  }

  _renderGuide() {
    return `
      <div style="font-size:13px;line-height:1.8">
        <h4 style="margin-bottom:12px">快速入门</h4>

        <div style="margin-bottom:16px">
          <strong>1. 首次扫描</strong>
          <p>点击右侧"刷新数据"按钮，系统将：</p>
          <ul style="margin:8px 0;padding-left:20px">
            <li>获取市场数据</li>
            <li>运行选股策略</li>
            <li>计算因子评分</li>
            <li>生成交易信号</li>
            <li>优化投资组合</li>
          </ul>
        </div>

        <div style="margin-bottom:16px">
          <strong>2. 查看结果</strong>
          <p>扫描完成后，各标签页会显示：</p>
          <ul style="margin:8px 0;padding-left:20px">
            <li><b>组合优化</b>：推荐的持仓配置</li>
            <li><b>选股结果</b>：候选股票列表</li>
            <li><b>风控中心</b>：风险指标和建议</li>
            <li><b>信号追踪</b>：历史信号准确率</li>
          </ul>
        </div>

        <div style="margin-bottom:16px">
          <strong>3. 策略回测</strong>
          <p>切换到"策略回测"标签页：</p>
          <ul style="margin:8px 0;padding-left:20px">
            <li>输入股票代码（逗号分隔）</li>
            <li>设置回测天数和止损止盈</li>
            <li>点击"运行回测"</li>
            <li>查看收益曲线和统计指标</li>
          </ul>
        </div>

        <div style="margin-bottom:16px">
          <strong>4. 个性化设置</strong>
          <p>在"设置"标签页可以：</p>
          <ul style="margin:8px 0;padding-left:20px">
            <li>切换主题（亮色/暗色/高对比度）</li>
            <li>调整字体大小</li>
            <li>设置语言（中文/英文）</li>
            <li>配置通知偏好</li>
            <li>创建告警规则</li>
          </ul>
        </div>
      </div>
    `;
  }

  _renderShortcuts() {
    const shortcuts = [
      { key: 'j / k', desc: '切换标签页（上/下）' },
      { key: '/', desc: '聚焦搜索框' },
      { key: 'Esc', desc: '关闭弹窗/下拉菜单' },
      { key: 'r', desc: '刷新数据' },
      { key: '?', desc: '显示快捷键帮助' },
      { key: 'Ctrl+K', desc: '全局搜索' },
      { key: 'Tab', desc: '在元素间导航' },
      { key: 'Enter/Space', desc: '激活按钮/链接' },
      { key: 'Arrow Keys', desc: '在表格中导航' },
    ];

    return `
      <div style="font-size:13px">
        <h4 style="margin-bottom:12px">键盘快捷键</h4>
        <table style="width:100%;border-collapse:collapse">
          <thead>
            <tr>
              <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border)">快捷键</th>
              <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border)">功能</th>
            </tr>
          </thead>
          <tbody>
            ${shortcuts.map(s => `
              <tr>
                <td style="padding:8px;border-bottom:1px solid var(--border)">
                  <kbd style="background:var(--bg);padding:2px 6px;border-radius:3px;font-family:monospace;font-size:12px">${s.key}</kbd>
                </td>
                <td style="padding:8px;border-bottom:1px solid var(--border)">${s.desc}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }
}

// 单例
export const helpCenter = new HelpCenter();
