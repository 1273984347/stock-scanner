var SYM = {
  scanner:   '<path d="M3 12.5l3.5-4 2.8 2.4L14 5.5"/><path d="M14 5.5h-3.2M14 5.5v3.2"/>',
  portfolio: '<path d="M3 3h6v8H3zM11 3h6v5h-6zM11 10h6v7h-6zM3 13h6v4H3z"/>',
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
var TABS = [
  { t:'热门板块', rows:[
    ['603960','克来机电','机械设备','78.5','3.21','9.50','1.82','1284.0','2.1'],
    ['000021','深科技','电子','76.2','-1.04','6.30','3.44','2150.0','0.8'],
    ['002261','拓维信息','计算机','74.8','2.87','9.50','-0.52','1180.0','3.6'],
    ['603039','全筑股份','建筑装饰','72.1','1.15','3.00','0.31','410.0','0.4'],
    ['002651','利君股份','国防军工','70.6','-0.42','2.50','1.04','620.0','1.1'],
    ['002872','天圣制药','医药商业','69.3','0.88','2.50','0.22','380.0','0.3'],
    ['600280','中央商场','商业贸易','68.0','-1.76','2.50','-0.88','290.0','0.6'],
    ['000158','常山北明','软件开发','66.4','4.52','4.30','2.10','1450.0','4.2'],
    ['002912','中新赛克','网络安全','65.1','1.62','3.10','0.44','880.0','1.2'],
    ['600171','上海贝岭','半导体','63.8','-2.24','2.80','-1.02','760.0','0.9'],
    ['000977','浪潮信息','计算机','62.4','1.88','2.50','0.77','1180.0','2.4'],
    ['300474','景嘉微','半导体','61.7','-0.66','2.10','1.34','540.0','3.1']
  ]},
  { t:'涨停复盘', rows:[
    ['002261','拓维信息','计算机','74.8','2.87','9.50','-0.52','1180.0','3.6'],
    ['000158','常山北明','软件开发','66.4','4.52','4.30','2.10','1450.0','4.2'],
    ['603039','全筑股份','建筑装饰','72.1','1.15','3.00','0.31','410.0','0.4'],
    ['603960','克来机电','机械设备','78.5','3.21','9.50','1.82','1284.0','2.1'],
    ['002651','利君股份','国防军工','70.6','-0.42','2.50','1.04','620.0','1.1'],
    ['000977','浪潮信息','计算机','62.4','1.88','2.50','0.77','1180.0','2.4'],
    ['300474','景嘉微','半导体','61.7','-0.66','2.10','1.34','540.0','3.1'],
    ['002912','中新赛克','网络安全','65.1','1.62','3.10','0.44','880.0','1.2']
  ]},
  { t:'龙虎榜', rows:[
    ['603960','克来机电','机械设备','78.5','3.21','9.50','1.82','1284.0','2.1'],
    ['002872','天圣制药','医药商业','69.3','0.88','2.50','0.22','380.0','0.3'],
    ['600280','中央商场','商业贸易','68.0','-1.76','2.50','-0.88','290.0','0.6'],
    ['000021','深科技','电子','76.2','-1.04','6.30','3.44','2150.0','0.8'],
    ['600171','上海贝岭','半导体','63.8','-2.24','2.80','-1.02','760.0','0.9'],
    ['002651','利君股份','国防军工','70.6','-0.42','2.50','1.04','620.0','1.1']
  ]}
];
var COLS = ['代码','名称','行业','评分','涨跌','仓位','量比','市值(亿)','换手%'];
function el(t,c,x){var e=document.createElement(t); if(c)e.className=c; if(x!=null)e.textContent=x; return e;}
function svg(p,vb){var s=document.createElementNS('http://www.w3.org/2000/svg','svg'); s.setAttribute('viewBox',vb||'0 0 20 20'); s.innerHTML=p; return s;}
var activeTab=0, activeRow=0, selCol=3;

function renderSidebar(active){
  var sb=document.getElementById('sidebar'); sb.innerHTML='';
  NAV.forEach(function(g){
    if(g.g) sb.appendChild(el('div','sb-head',g.g));
    g.items.forEach(function(it){
      var d=el('div','sb-item'+(it.id===active?' on':''));
      d.appendChild(svg(SYM[it.id]||''));
      d.appendChild(el('span','sb-label',it.n));
      if(it.c) d.appendChild(el('span','count',it.c));
      d.addEventListener('click',function(){
        document.querySelectorAll('.sb-item').forEach(function(x){x.classList.remove('on');});
        d.classList.add('on');
      });
      sb.appendChild(d);
    });
  });
}

function renderSheet(){
  var t=document.getElementById('sheet'); t.innerHTML='';
  var th=el('thead'); var tr=el('tr');
  COLS.forEach(function(c,i){ tr.appendChild(el('th', (i<3?'l ':'')+(i===selCol?'sel':''), c)); });
  th.appendChild(tr); t.appendChild(th);
  var tb=el('tbody');
  var rows=TABS[activeTab].rows;
  rows.forEach(function(r,i){
    var row=el('tr');
    if(i===activeRow) row.className='on';
    r.forEach(function(c,j){
      var td=el('td', (j<3?'l ':'')+(j>=3?'n':''), c);
      if(j===4) td.className=(j<3?'l ':'n ')+(parseFloat(c)>=0?'up':'down');
      if(j===selCol) td.style.boxShadow='inset 2px 0 0 var(--accent)';
      row.appendChild(td);
    });
    row.addEventListener('click',function(){ activeRow=i; renderSheet(); renderFmt(); renderSum(); });
    tb.appendChild(row);
  });
  t.appendChild(tb);
}

function renderSum(){
  var sb=document.getElementById('sumbar'); sb.innerHTML='';
  var rows=TABS[activeTab].rows;
  var sum=0, up=0, wsum=0;
  rows.forEach(function(r){ sum+=parseFloat(r[3]); wsum+=parseFloat(r[5]); if(parseFloat(r[4])>0) up++; });
  var avg=(sum/rows.length).toFixed(2);
  var items=[['数量',String(rows.length)],['均分',avg],['上涨',up+' / '+rows.length],['仓位和',wsum.toFixed(1)+'%']];
  items.forEach(function(p){ var c=el('div','cell'); c.appendChild(el('span',null,p[0])); c.appendChild(el('b',null,p[1])); sb.appendChild(c); });
  var sp=el('div','sp'); sb.appendChild(sp);
  var c2=el('div','cell'); c2.appendChild(el('span',null,'信号准确率')); c2.appendChild(el('b',null,'92%')); sb.appendChild(c2);
}

function renderFmt(){
  var f=document.getElementById('fmt'); f.innerHTML='';
  var r=TABS[activeTab].rows[activeRow];
  var s1=el('div','fmt-sec');
  s1.appendChild(el('div','fmt-h','单元格'));
  var r1=el('div','fmt-row'); r1.appendChild(el('span','k','值')); r1.appendChild(el('span','v',r[3])); s1.appendChild(r1);
  var r2=el('div','fmt-row'); r2.appendChild(el('span','k','名称')); r2.appendChild(el('span','v',r[1])); s1.appendChild(r2);
  var r3=el('div','fmt-row'); r3.appendChild(el('span','k','行业')); r3.appendChild(el('span','v',r[2])); s1.appendChild(r3);
  f.appendChild(s1);
  var s2=el('div','fmt-sec');
  s2.appendChild(el('div','fmt-h','涨跌样式'));
  var tg=el('div','fmt-toggle');
  [['china','红涨绿跌'],['international','绿涨红跌']].forEach(function(x){
    var b=el('button', cs===x[0]?'on':'', x[1]);
    b.addEventListener('click',function(){ applyCs(x[0]); renderFmt(); renderSheet(); });
    tg.appendChild(b);
  });
  s2.appendChild(tg);
  var sw=el('div','fmt-row'); sw.style.marginTop='8px';
  var sp2=el('span'); sp2.innerHTML='<i class="swatch" style="background:var(--up)"></i>上涨';
  sw.appendChild(sp2); s2.appendChild(sw);
  var sw2=el('div','fmt-row');
  var sp3=el('span'); sp3.innerHTML='<i class="swatch" style="background:var(--down)"></i>下跌';
  sw2.appendChild(sp3); s2.appendChild(sw2);
  f.appendChild(s2);
  var s3=el('div','fmt-sec');
  s3.appendChild(el('div','fmt-h','列'));
  var tg2=el('div','fmt-toggle');
  COLS.forEach(function(c,i){
    var b=el('button', i===selCol?'on':'', c.slice(0,2));
    b.addEventListener('click',function(){ selCol=i; renderSheet(); renderFmt(); });
    tg2.appendChild(b);
  });
  s3.appendChild(tg2);
  f.appendChild(s3);
  var s4=el('div','fmt-sec');
  s4.appendChild(el('div','fmt-h','说明'));
  var p=el('div','fmt-row'); p.style.display='block'; p.style.fontSize='12px'; p.style.lineHeight='1.6'; p.style.color='var(--label-2)';
  p.textContent='评分来自四维打分（基本面/资金流/新闻情绪/技术面），缺维剔除后归一权重。';
  s4.appendChild(p);
  f.appendChild(s4);
}

function renderSeg(){
  var seg=document.getElementById('tabSeg'); seg.innerHTML='';
  TABS.forEach(function(t,i){
    var b=el('button', i===activeTab?'on':'', t.t);
    b.addEventListener('click',function(){ activeTab=i; activeRow=0; renderSeg(); renderSheet(); renderSum(); renderFmt(); });
    seg.appendChild(b);
  });
}

var theme=localStorage.getItem('apTheme')||'system';
function applyTheme(t){
  theme=t; localStorage.setItem('apTheme',t);
  document.documentElement.setAttribute('data-theme',t);
  var b=document.getElementById('themeBtn');
  b.innerHTML='<svg viewBox="0 0 20 20">'+THEME_SVG[t]+'</svg>';
  b.title = t==='dark'?'深色':(t==='light'?'浅色':'跟随系统');
}
var cs=localStorage.getItem('apCs')||'china';
function applyCs(v){
  cs=v; localStorage.setItem('apCs',v);
  document.documentElement.setAttribute('data-color-scheme',v);
  document.getElementById('csBtn').textContent = v==='china'?'红涨绿跌':'绿涨红跌';
}
document.getElementById('themeBtn').addEventListener('click',function(){
  var ids=['dark','light','system'];
  applyTheme(ids[(ids.indexOf(theme)+1)%ids.length]);
});
document.getElementById('csBtn').addEventListener('click',function(){ applyCs(cs==='china'?'international':'china'); renderFmt(); renderSheet(); });
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',function(){
  if(theme==='system') document.documentElement.setAttribute('data-theme','system');
});

var st=document.getElementById('statusbar');
st.innerHTML='<span><i class="dot on"></i>WebSocket 已连接</span><span>数据源 TDX</span>'+
  '<span>最后更新 14:02:31</span><span style="flex:1"></span>'+
  '<span>沪深300 <span class="num up">3,412.55 +0.82%</span></span>';

var ad=document.getElementById('addr'), fm=document.getElementById('formula');
ad.textContent = 'A'+(activeRow+1);
fm.innerHTML = '<b>'+TABS[activeTab].rows[activeRow][0]+'</b> · '+TABS[activeTab].rows[activeRow][1]+' · '+TABS[activeTab].rows[activeRow][2];

applyTheme(theme); applyCs(cs);
renderSidebar('scanner'); renderSeg(); renderSheet(); renderSum(); renderFmt();
