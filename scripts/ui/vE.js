/* SF Symbols 风格线性图标 */
var SYM = {
  portfolio: '<path d="M3 3h6v8H3zM11 3h6v5h-6zM11 10h6v7h-6zM3 13h6v4H3z"/>',
  scanner:   '<path d="M3 12.5l3.5-4 2.8 2.4L14 5.5"/><path d="M14 5.5h-3.2M14 5.5v3.2"/>',
  risk:      '<path d="M10 2.5l6.5 2.6v4.4c0 3.5-2.7 6.2-6.5 7.6-3.8-1.4-6.5-4.1-6.5-7.6V5.1z"/>',
  positions: '<rect x="2.5" y="4" width="15" height="10" rx="2"/><path d="M2.5 8h15M7 4v10"/>',
  exposures: '<circle cx="10" cy="10" r="6.5"/><circle cx="10" cy="10" r="2.5"/>',
  signals:   '<circle cx="10" cy="10" r="2.2"/><path d="M4.8 4.8a7.4 7.4 0 000 10.4M15.2 4.8a7.4 7.4 0 010 10.4"/>',
  trades:    '<path d="M3 12l3.6-4 2.8 2.4L13.6 5.6"/><path d="M3 16h14"/>',
  backtest:  '<circle cx="10" cy="10" r="6.8"/><path d="M10 5.8V10l2.8 2"/>',
  strategy:  '<path d="M4.5 2.8v14.4M15.5 2.8v14.4M4.5 7h11M4.5 13h11"/>',
  audit:     '<path d="M5 2.8h10v14.4H5z"/><path d="M7.6 6.2h4.8M7.6 9h4.8M7.6 11.8h3"/>',
  reports:   '<rect x="3" y="2.8" width="14" height="14.4" rx="2"/><path d="M6 6.4h8M6 9.4h8M6 12.4h5"/>',
  notifications: '<path d="M10 3a4.2 4.2 0 00-4.2 4.2V10l-1.6 2.6h11.6L14.2 10V7.2A4.2 4.2 0 0010 3z"/><path d="M8.4 15.2a1.9 1.9 0 003.2 0"/>',
  settings:  '<circle cx="10" cy="10" r="2.6"/><path d="M10 2.4v2.2M10 15.4v2.2M17.6 10h-2.2M4.6 10h-2.2M15.4 4.6l-1.6 1.6M6.2 13.8l-1.6 1.6M15.4 15.4l-1.6-1.6M6.2 6.2L4.6 4.6"/>'
};
var THEME_SVG = {
  light: '<circle cx="10" cy="10" r="3.8"/>',
  dark: '<path d="M15.8 11.2A6.6 6.6 0 019.4 4.8a6.6 6.6 0 106.4 6.4z"/>',
  system: '<rect x="2.4" y="3.6" width="15.2" height="10.6" rx="2"/><path d="M7.4 17.4h5.2"/>'
};
var NAV = [
  { g:'扫描', items:[ {id:'scanner',n:'选股结果',c:'30'},{id:'signals',n:'信号追踪',c:'92%'} ] },
  { g:'组合', items:[ {id:'portfolio',n:'优化组合',c:'16'},{id:'positions',n:'持仓概况',c:'65%'},{id:'exposures',n:'因子暴露',c:'9'} ] },
  { g:'风险', items:[ {id:'risk',n:'风控中心',c:'3'},{id:'backtest',n:'策略回测',c:'6'} ] },
  { g:'记录', items:[ {id:'trades',n:'交易历史',c:'24'},{id:'audit',n:'决策审计',c:'15'},{id:'reports',n:'历史报告',c:'30'} ] },
  { g:'', items:[ {id:'strategy',n:'策略对比'},{id:'notifications',n:'通知中心'},{id:'settings',n:'设置'} ] }
];
var TILES = [
  {n:'贵州茅台',c:'600519.SH',p:'1682.50',d:1.24,sp:[52,54,53,58,61,60,66,70,68,74,78,82]},
  {n:'宁德时代',c:'300750.SZ',p:'196.80',d:2.07,sp:[44,46,45,49,52,50,55,57,56,60,63,66]},
  {n:'深科技',c:'000021.SZ',p:'12.44',d:-1.04,sp:[60,58,62,59,55,52,50,48,46,45,44,43]},
  {n:'拓维信息',c:'002261.SZ',p:'48.66',d:2.87,sp:[38,40,43,46,49,53,58,62,66,70,74,79]},
  {n:'五粮液',c:'000858.SZ',p:'142.30',d:-0.86,sp:[58,56,54,52,51,49,48,47,46,46,45,44]},
  {n:'克来机电',c:'603960.SH',p:'31.85',d:3.21,sp:[40,42,45,49,53,56,60,65,69,73,78,84]}
];
var HOLDINGS = [
  ['600519','贵州茅台','食品饮料','12.5%','1682.50','+1.24','+8.4%'],
  ['300750','宁德时代','电池','11.2%','196.80','+2.07','+15.2%'],
  ['000858','五粮液','白酒','9.8%','142.30','-0.86','-3.1%'],
  ['601318','中国平安','保险','8.4%','48.62','+0.35','+2.2%'],
  ['002415','海康威视','安防','7.1%','31.14','-1.12','-4.7%'],
  ['600036','招商银行','银行','6.5%','34.52','+0.58','+5.1%'],
  ['603960','克来机电','机械设备','9.5%','31.85','+3.21','+6.2%'],
  ['000021','深科技','电子','6.3%','12.44','-1.04','-4.7%']
];
var PICKS = [
  ['603960','克来机电','机械设备','78.5','3.21','9.5'],
  ['000021','深科技','电子','76.2','-1.04','6.3'],
  ['002261','拓维信息','计算机','74.8','2.87','9.5'],
  ['603039','全筑股份','建筑装饰','72.1','1.15','3.0'],
  ['002651','利君股份','国防军工','70.6','-0.42','2.5'],
  ['002872','天圣制药','医药商业','69.3','0.88','2.5'],
  ['600280','中央商场','商业贸易','68.0','-1.76','2.5'],
  ['000158','常山北明','软件开发','66.4','4.52','4.3']
];
var FACTORS = [['量价结构',82],['资金流入',74],['题材热度',68],['技术形态',61],['估值分位',44],['机构持仓',39]];
function el(t,c,x){var e=document.createElement(t); if(c)e.className=c; if(x!=null)e.textContent=x; return e;}
function svg(path,vb){var s=document.createElementNS('http://www.w3.org/2000/svg','svg'); s.setAttribute('viewBox',vb||'0 0 20 20'); s.innerHTML=path; return s;}
var activeNav='scanner', activeTile=0, activeRow=0;

function renderSidebar(active){
  var sb=document.getElementById('sidebar'); sb.innerHTML='';
  NAV.forEach(function(g){
    if(g.g) sb.appendChild(el('div','sb-head',g.g));
    g.items.forEach(function(it){
      var d=el('div','sb-item'+(it.id===active?' on':''));
      d.appendChild(svg(SYM[it.id]||''));
      d.appendChild(el('span','sb-label',it.n));
      if(it.c) d.appendChild(el('span','count',it.c));
      d.addEventListener('click',function(){ select(it.id); });
      sb.appendChild(d);
    });
  });
}

function mkTable(cols, rows, getChg, onRow){
  var tb=el('table');
  var th=el('thead'); var tr=el('tr');
  cols.forEach(function(c,i){ tr.appendChild(el('th', i>=3?'r':null, c)); });
  th.appendChild(tr); tb.appendChild(th);
  var tbody=el('tbody');
  rows.forEach(function(r,i){
    var row=el('tr');
    if(i===activeRow) row.style.background='var(--accent-fill)';
    r.forEach(function(c,j){
      var td=el('td', j>=3?'r':(j===1?'n':''), c);
      var ci=getChg?getChg(j):-1;
      if(ci>=0) td.className='r '+(parseFloat(c)>=0?'up':'down');
      row.appendChild(td);
    });
    row.addEventListener('click',function(){ activeRow=i; onRow(); });
    tbody.appendChild(row);
  });
  tb.appendChild(tbody);
  return tb;
}

function renderMain(){
  var sc=document.getElementById('scroll'); sc.innerHTML='';
  var hero=el('div','hero');
  var title='盘后复盘', sub='2026-11-28 · 收盘后 · 数据源 TDX';
  if(activeNav!=='scanner' && activeNav!=='signals'){
    var found=null;
    NAV.forEach(function(g){ g.items.forEach(function(i){ if(i.id===activeNav) found=i; }); });
    if(found){ title=found.n; }
  }
  hero.appendChild(el('h1',null,title));
  hero.appendChild(el('div','sub',sub));
  var hr=el('div','hero-row');
  [['市场情绪','0.94','big'],['建议仓位','64%',''],['组合夏普','0.06',''],['候选股','30',''],['持仓','16','']].forEach(function(s){
    var d=el('div','hero-stat');
    d.appendChild(el('div','k',s[0]));
    d.appendChild(el('div','v'+(s[2]?' big':''),s[1]));
    hr.appendChild(d);
  });
  hero.appendChild(hr);
  sc.appendChild(hero);

  if(activeNav==='scanner' || activeNav==='signals'){
    var sh=el('div','sec-h');
    sh.appendChild(el('h2',null,'观察列表'));
    sh.appendChild(el('span','sub','6 只标的'));
    sc.appendChild(sh);
    var grid=el('div','grid');
    TILES.forEach(function(t,i){
      var tile=el('div','tile');
      tile.appendChild(el('div','t',t.n));
      tile.appendChild(el('div','c',t.c));
      var pr=el('div','price');
      pr.appendChild(el('span','p',t.p));
      pr.appendChild(el('span','d '+(t.d>=0?'up':'down'),(t.d>=0?'+':'')+t.d.toFixed(2)+'%'));
      tile.appendChild(pr);
      var sp=el('div','spark');
      var base=t.sp[0];
      t.sp.forEach(function(h,j){ var i2=el('i'); i2.style.height=h+'%'; i2.className = h>=base ? 'u' : 'd'; i2.style.opacity = 0.35 + 0.5*(j/(t.sp.length-1)); sp.appendChild(i2); });
      tile.appendChild(sp);
      tile.addEventListener('click',function(){ activeTile=i; renderMain(); renderInspector(); });
      grid.appendChild(tile);
    });
    sc.appendChild(grid);

    var sh2=el('div','sec-h');
    sh2.appendChild(el('h2',null,'当前持仓'));
    sh2.appendChild(el('span','sub','按仓位排序 · 16 只'));
    sc.appendChild(sh2);
    var ins=el('div','inset');
    ins.appendChild(mkTable(['代码','名称','行业','仓位','现价','涨跌','盈亏'], HOLDINGS,
      function(j){ return (j===5||j===6)?j:-1; },
      function(){ renderMain(); renderInspector(); }));
    sc.appendChild(ins);
  } else {
    var sh=el('div','sec-h');
    sh.appendChild(el('h2',null,'候选清单'));
    sh.appendChild(el('span','sub','30 只 · 已按评分排序'));
    sc.appendChild(sh);
    var ins=el('div','inset');
    ins.appendChild(mkTable(['代码','名称','行业','评分','涨跌','仓位'], PICKS,
      function(j){ return j===4?j:-1; },
      function(){ renderMain(); renderInspector(); }));
    sc.appendChild(ins);
  }
}

function renderInspector(){
  var ins=document.getElementById('inspector'); ins.innerHTML='';
  var b1=el('div','ins-block');
  b1.appendChild(el('div','ins-k','选中标的'));
  var t=TILES[activeTile];
  b1.appendChild(el('div','ins-name',t.n));
  b1.appendChild(el('div','ins-code',t.c));
  ins.appendChild(b1);
  var b2=el('div','ins-block');
  b2.appendChild(el('div','ins-k','评分构成'));
  FACTORS.forEach(function(f){
    var r=el('div','ins-row');
    r.appendChild(el('span','k',f[0]));
    r.appendChild(el('span','v',String(f[1])));
    b2.appendChild(r);
    var bar=el('div','bar'); var i2=el('i'); i2.style.width=f[1]+'%'; bar.appendChild(i2);
    b2.appendChild(bar);
  });
  ins.appendChild(b2);
  var b3=el('div','ins-block');
  b3.appendChild(el('div','ins-k','风险标记'));
  [['ST 风险','无'],['退市风险','无'],['流动性','充足'],['股权质押','3.2%'],['拥挤度','中位']].forEach(function(p){
    var r=el('div','ins-row');
    r.appendChild(el('span','k',p[0]));
    r.appendChild(el('span','v',p[1]));
    b3.appendChild(r);
  });
  ins.appendChild(b3);
}

function select(id){ activeNav=id; renderSidebar(id); renderMain(); }

var theme=localStorage.getItem('apTheme')||'system';
function applyTheme(t){
  theme=t; localStorage.setItem('apTheme',t);
  document.documentElement.setAttribute('data-theme',t);
  var seg=document.getElementById('themeSeg');
  seg.innerHTML='';
  [['light','浅色'],['dark','深色'],['system','自动']].forEach(function(x){
    var b=el('button', t===x[0]?'on':'', x[1]);
    b.addEventListener('click',function(){ applyTheme(x[0]); });
    seg.appendChild(b);
  });
}
var cs=localStorage.getItem('apCs')||'china';
function applyCs(v){
  cs=v; localStorage.setItem('apCs',v);
  document.documentElement.setAttribute('data-color-scheme',v);
  var b=document.getElementById('csBtn');
  b.innerHTML='<svg viewBox="0 0 20 20"><path d="M3.5 13.5l4-4.5 3 3 5-6"/></svg>';
  b.title = v==='china' ? '红涨绿跌（A股）' : '绿涨红跌（国际）';
}
document.getElementById('csBtn').addEventListener('click',function(){ applyCs(cs==='china'?'international':'china'); });
document.getElementById('collapseBtn').addEventListener('click',function(){
  document.getElementById('sidebar').classList.toggle('collapsed');
});
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',function(){
  if(theme==='system') document.documentElement.setAttribute('data-theme','system');
});

var st=document.getElementById('statusbar');
st.innerHTML='<span><i class="dot on"></i>WebSocket 已连接</span><span>数据源 TDX</span>'+
  '<span>最后更新 14:02:31</span><span style="flex:1"></span>'+
  '<span>沪深300 <span class="num up">3,412.55 +0.82%</span></span>'+
  '<span>候选 <span class="num">30</span> · 持仓 <span class="num">16</span></span>';

applyTheme(theme); applyCs(cs); renderSidebar(activeNav); renderMain(); renderInspector();
