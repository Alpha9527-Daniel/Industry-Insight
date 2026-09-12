/* Industry-Insight — 前端渲染(零依赖,手写 SVG 图表) */
(function () {
  'use strict';

  var DATA = 'data/';
  var state = {
    latest: null,
    etf: null,
    news: null,
    sort: { key: null, dir: 'desc' },
    range: 3,          // 走势图区间(年)
    current: null,     // 当前行业代码
    dates: [],         // 可回看的交易日 ['20260830', …]
    labels: {},        // 交易日 -> 'YYYY-MM-DD'
    day: null          // 当前查看的交易日
  };

  /* ------------------------------------------------------------ 工具函数 */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html !== undefined) n.innerHTML = html;
    return n;
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function num(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return null;
    return Number(v).toFixed(digits === undefined ? 2 : digits);
  }
  function signed(v, digits) {
    var s = num(v, digits);
    if (s === null) return null;
    return (Number(v) > 0 ? '+' : '') + s;
  }
  /* 数据文件一律带时间戳请求:GitHub Pages 的 CDN 会把"新增路径的 404"缓存一段时间
     (dates.json / daily/ 都是新增路径),不绕这一层会出现"下拉能点、选了没反应"。
     history/ 体积大且内容稳定,留给浏览器缓存。 */
  function loadJSON(path) {
    var url = DATA + path;
    if (path.indexOf('history/') !== 0) {
      url += (path.indexOf('?') >= 0 ? '&' : '?') + 'v=' + Date.now();
    }
    return fetch(url, { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error(path + ' HTTP ' + r.status);
      return r.json();
    });
  }

  /* 分位数配色:invert=false 越高越红(PE/换手),true 越高越绿(ROE) */
  function pctColor(pct, invert) {
    var p = Math.max(0, Math.min(100, Number(pct) || 0)) / 100;
    var r, g, b;
    if (!invert) {           // 绿 -> 黄 -> 红
      r = 16 + (239 - 16) * p;
      g = 185 + (68 - 185) * p;
      b = 129 + (68 - 129) * p;
    } else {                 // 红 -> 黄 -> 绿
      r = 239 + (16 - 239) * p;
      g = 68 + (185 - 68) * p;
      b = 68 + (129 - 68) * p;
    }
    return 'rgb(' + Math.round(r) + ',' + Math.round(g) + ',' + Math.round(b) + ')';
  }

  /* 分位数条(0-100) */
  function pbar(v, invert) {
    if (v === null || v === undefined || isNaN(v)) return '<span class="na">--</span>';
    var p = Math.max(0, Math.min(100, Number(v)));
    var color = pctColor(p, invert);
    var dark = p > 62 || p < 22;   // 深底/亮底上的文字用白字
    return '<div class="pbar">' +
      '<div class="pbar-fill" style="width:' + p.toFixed(1) + '%;background:' + color + '"></div>' +
      '<div class="pbar-val' + (dark ? ' on-dark' : '') + '">' + p.toFixed(1) + '</div>' +
      '</div>';
  }

  /* 正负色块 */
  function box(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return '<span class="na">--</span>';
    var n = Number(v);
    var cls = n > 0 ? 'box-pos' : (n < 0 ? 'box-neg' : 'box-zero');
    return '<span class="box ' + cls + '">' + (signed(n, digits === undefined ? 2 : digits)) + '%</span>';
  }

  /* ------------------------------------------------------------ 侧边栏 */
  function buildNav(industries) {
    var host = $('#industry-nav');
    host.innerHTML = '';
    industries.forEach(function (ind) {
      var a = el('a', 'nav-item');
      a.href = '#/' + ind.code;
      a.dataset.code = ind.code;
      a.innerHTML = '<span class="nav-dot"></span><span class="nav-name">' + esc(ind.name) +
        '</span><span class="nav-code">' + esc(ind.code) + '</span>';
      host.appendChild(a);
    });
    $('#nav-count').textContent = industries.length;
  }

  function setActiveNav(code) {
    var items = document.querySelectorAll('.nav-item');
    for (var i = 0; i < items.length; i++) {
      var isActive = (items[i].dataset.code || '') === (code || '');
      items[i].classList.toggle('active', isActive);
    }
  }

  /* -------------------------------------------------------- 首页大表 */
  var COLS = [
    { key: 'pe',          label: 'PE Ratio', unit: 'TTM',     grp: 'valuation',  fmt: 'x', w: 62 },
    { key: 'pePct',       label: 'PE分位',   unit: '5Y %',    grp: 'valuation',  type: 'pbar', invert: false, w: 74 },
    { key: 'turnover',    label: 'Turnover', unit: '10D avg', grp: 'crowd',      fmt: '%', w: 62 },
    { key: 'turnoverPct', label: '换手分位', unit: '5Y %',    grp: 'crowd',      type: 'pbar', invert: false, w: 74 },
    { key: 'roe',         label: 'ROE',      unit: 'TTM %',   grp: 'prosperity', fmt: '%', w: 58 },
    { key: 'roeYoY',      label: 'ROE同比',  unit: 'YoY',     grp: 'prosperity', type: 'box', w: 72 },
    { key: 'roePct',      label: 'ROE分位',  unit: '5Y %',    grp: 'prosperity', type: 'pbar', invert: true, w: 74 },
    { key: 'roeCap',      label: 'ROE_Cap',  unit: 'TTM %',   grp: 'prosperity', fmt: '%', w: 62 },
    { key: 'roeCapYoY',   label: 'Cap同比',  unit: 'YoY',     grp: 'prosperity', type: 'box', w: 72 },
    { key: 'roeCapPct',   label: 'Cap分位',  unit: '5Y %',    grp: 'prosperity', type: 'pbar', invert: true, w: 74 },
    { key: 'r1w',         label: 'Return',   unit: '1W %',    grp: 'momentum',   type: 'box', w: 72 },
    { key: 'r1m',         label: 'Return',   unit: '1M %',    grp: 'momentum',   type: 'box', w: 72 },
    { key: 'r1y',         label: 'Return',   unit: '1Y %',    grp: 'momentum',   type: 'box', w: 72 }
  ];

  function renderHome() {
    var host = $('#view-home');
    host.innerHTML = '';

    var wrap = el('div', 'table-wrap');
    var t = el('table', 'grid');
    var thead = el('thead');

    // 分组表头
    var grpRow = el('tr', 'group-row');
    grpRow.appendChild(el('th', '', '行业'));
    var groups = [
      { key: 'valuation', label: '估值 VALUATION', span: 0, cls: 'grp-valuation' },
      { key: 'crowd', label: '拥挤 CROWDING', span: 0, cls: 'grp-crowd' },
      { key: 'prosperity', label: '景气 PROSPERITY', span: 0, cls: 'grp-prosperity' },
      { key: 'momentum', label: '动量 MOMENTUM', span: 0, cls: 'grp-momentum' }
    ];
    COLS.forEach(function (c) {
      groups.filter(function (g) { return g.key === c.grp; })[0].span++;
    });
    groups.forEach(function (g) {
      var th = el('th', g.cls, g.label);
      th.colSpan = g.span;
      grpRow.appendChild(th);
    });
    thead.appendChild(grpRow);

    // 列头(可排序)
    var colRow = el('tr', 'col-row');
    colRow.appendChild(el('th', '', '申万一级'));
    COLS.forEach(function (c) {
      var th = el('th', 'sortable');
      th.dataset.key = c.key;
      th.style.width = c.w + 'px';
      th.innerHTML = esc(c.label) +
        '<br><span style="font-size:9px;color:var(--text-muted);font-weight:400">' + esc(c.unit) + '</span>' +
        '<span class="sort-icon">&#9650;</span>';
      th.addEventListener('click', function () { sortBy(c.key); });
      colRow.appendChild(th);
    });
    thead.appendChild(colRow);
    t.appendChild(thead);

    var tbody = el('tbody');
    tbody.id = 'grid-body';
    t.appendChild(tbody);
    wrap.appendChild(t);
    host.appendChild(wrap);

    // 图例
    var legend = el('div', 'legend');
    legend.innerHTML =
      '<span style="font-weight:600">图例</span>' +
      '<span>PE / 换手分位 (低→高)</span><div class="legend-bar legend-pe"></div>' +
      '<span style="margin-left:10px">ROE / ROE_Cap 分位 (低→高)</span><div class="legend-bar legend-roe"></div>' +
      '<span style="margin-left:10px">同比 / 收益率</span>' +
      '<span class="box box-pos">+0.00%</span><span class="box box-neg">-0.00%</span>' +
      '<span style="margin-left:10px;color:var(--text-muted)">点击表头排序 · 点击行业名进入详情</span>';
    host.appendChild(legend);

    fillRows($('#grid-body'), state.latest.industries.slice());
  }

  function fillRows(tbody, inds) {
    tbody.innerHTML = '';
    inds.forEach(function (ind) {
      var tr = el('tr');
      tr.dataset.code = ind.code;
      var td0 = el('td');
      td0.innerHTML = '<a class="ind-link" href="#/' + esc(ind.code) + '">' + esc(ind.name) +
        '<span class="code">' + esc(ind.code) + '</span></a>';
      tr.appendChild(td0);

      COLS.forEach(function (c) {
        var td = el('td');
        var v = ind[c.key];
        if (c.type === 'pbar') td.innerHTML = pbar(v, c.invert);
        else if (c.type === 'box') td.innerHTML = box(v, 2);
        else td.innerHTML = (v === null || v === undefined || isNaN(v))
          ? '<span class="na">--</span>'
          : '<span>' + num(v, 2) + c.fmt + '</span>';
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function sortBy(key) {
    if (state.sort.key === key) {
      state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
    } else {
      state.sort.key = key;
      state.sort.dir = 'desc';   // 首次点击:大值在前
    }
    var ths = document.querySelectorAll('.col-row th.sortable');
    for (var i = 0; i < ths.length; i++) {
      ths[i].classList.remove('sort-asc', 'sort-desc');
      ths[i].querySelector('.sort-icon').innerHTML = '&#9650;';
      if (ths[i].dataset.key === key) {
        ths[i].classList.add(state.sort.dir === 'asc' ? 'sort-asc' : 'sort-desc');
        ths[i].querySelector('.sort-icon').innerHTML = state.sort.dir === 'asc' ? '&#9650;' : '&#9660;';
      }
    }

    var inds = state.latest.industries.slice();
    inds.sort(function (a, b) {
      var xn = (a[key] === null || a[key] === undefined || isNaN(a[key])) ? -Infinity : Number(a[key]);
      var yn = (b[key] === null || b[key] === undefined || isNaN(b[key])) ? -Infinity : Number(b[key]);
      return state.sort.dir === 'asc' ? xn - yn : yn - xn;
    });
    fillRows($('#grid-body'), inds);
  }

  /* ------------------------------------------------------- 走势图(SVG) */
  function renderChart(host, hist) {
    var code = hist.code;
    var years = state.range;
    var now = new Date();
    var y0 = now.getFullYear() - years;
    var cutoffInt = Number(String(y0) +
      String(now.getMonth() + 1).padStart(2, '0') +
      String(now.getDate()).padStart(2, '0'));

    var idx = [];
    for (var i = 0; i < hist.d.length; i++) if (hist.d[i] >= cutoffInt) idx.push(i);
    if (idx.length < 2) idx = hist.d.map(function (_, k) { return k; });

    var dates = idx.map(function (i) { return hist.d[i]; });
    var closes = idx.map(function (i) { return hist.c[i]; });
    var amts = idx.map(function (i) { return hist.a[i]; });

    // 画布宽度取容器实测像素宽:viewBox 与实际像素 1:1 时不会被 preserveAspectRatio
    // 等比缩放后水平居中(否则图看起来比容器窄,而鼠标仍按整宽换算,对不上)
    var hostW = host.getBoundingClientRect().width || host.clientWidth || 1000;
    var W = Math.max(360, Math.round(hostW));
    var H = 240, PAD_L = 54, PAD_R = 12, PAD_T = 10, PAD_B = 18;
    var VOL_H = 60, GAP = 16;
    var plotW = W - PAD_L - PAD_R;

    var valid = closes.filter(function (v) { return v !== null; });
    var cMin = Math.min.apply(null, valid), cMax = Math.max.apply(null, valid);
    var pad = (cMax - cMin) * 0.06 || 1;
    cMin -= pad; cMax += pad;

    var validAmt = amts.filter(function (v) { return v !== null && v !== undefined; });
    var aMax = validAmt.length ? Math.max.apply(null, validAmt) : 0;

    var x = function (i) { return PAD_L + (plotW * i) / Math.max(1, dates.length - 1); };
    var y = function (v) { return PAD_T + (H - PAD_T - PAD_B) * (1 - (v - cMin) / (cMax - cMin)); };
    var volTop = H + GAP;
    var vy = function (v) { return volTop + VOL_H * (1 - (v / (aMax || 1))); };
    var totalH = volTop + VOL_H + 18;

    var linePts = [];
    for (var j = 0; j < closes.length; j++) {
      if (closes[j] === null) continue;
      linePts.push(x(j).toFixed(1) + ',' + y(closes[j]).toFixed(1));
    }
    var areaPts = linePts.slice();
    areaPts.push(x(closes.length - 1).toFixed(1) + ',' + (H - PAD_B));
    areaPts.push(x(0).toFixed(1) + ',' + (H - PAD_B));

    var up = closes[closes.length - 1] >= closes[0];
    var lineColor = up ? '#10b981' : '#ef4444';
    var gradId = 'g_' + code;

    var ticks = '';
    for (var k = 0; k <= 4; k++) {
      var val = cMin + (cMax - cMin) * k / 4;
      var yy = y(val).toFixed(1);
      ticks += '<line x1="' + PAD_L + '" y1="' + yy + '" x2="' + (W - PAD_R) + '" y2="' + yy +
        '" stroke="#16202e" stroke-width="1"/>' +
        '<text x="' + (PAD_L - 7) + '" y="' + (Number(yy) + 3.5).toFixed(1) +
        '" fill="#64748b" font-size="10" text-anchor="end" font-family="JetBrains Mono, monospace">' +
        val.toFixed(0) + '</text>';
    }
    var volTicks = '';
    for (var m = 0; m <= 2; m++) {
      var av = aMax * m / 2;
      var ay = vy(av).toFixed(1);
      volTicks += '<line x1="' + PAD_L + '" y1="' + ay + '" x2="' + (W - PAD_R) + '" y2="' + ay +
        '" stroke="#131c29" stroke-width="1"/>' +
        '<text x="' + (PAD_L - 7) + '" y="' + (Number(ay) + 3.5).toFixed(1) +
        '" fill="#475569" font-size="9" text-anchor="end" font-family="JetBrains Mono, monospace">' +
        (av >= 1000 ? (av / 1000).toFixed(1) + 'k' : av.toFixed(0)) + '</text>';
    }

    // 成交额柱(>700 根时抽样合并,保证渲染性能)
    var step = Math.max(1, Math.ceil(dates.length / 700));
    var bars = '';
    var barW = Math.max(1, (plotW / dates.length) * step * 0.72);
    for (var b = 0; b < amts.length; b += step) {
      var v2 = amts[b];
      if (v2 === null || v2 === undefined) continue;
      var bx = x(b), bh = Math.max(0.6, volTop + VOL_H - vy(v2));
      var bUp = b > 0 && closes[b] >= closes[b - 1];
      bars += '<rect x="' + (bx - barW / 2).toFixed(1) + '" y="' + vy(v2).toFixed(1) +
        '" width="' + barW.toFixed(1) + '" height="' + bh.toFixed(1) +
        '" fill="' + (bUp ? '#0e7a5f' : '#8f3030') + '" opacity="0.75"/>';
    }

    var xLabels = '';
    for (var q = 0; q <= 3; q++) {
      var pos = Math.round((dates.length - 1) * q / 3);
      var ds = String(dates[pos]);
      xLabels += '<text x="' + x(pos).toFixed(1) + '" y="' + (totalH - 3) +
        '" fill="#64748b" font-size="10" text-anchor="middle" font-family="JetBrains Mono, monospace">' +
        ds.slice(0, 4) + '-' + ds.slice(4, 6) + '</text>';
    }

    host.innerHTML = '';
    var svgNS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + totalH);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', totalH);
    // 不用默认的等比留白:窗口变化时图形仍铺满容器,鼠标换算与显示始终一致
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.innerHTML =
      '<defs><linearGradient id="' + gradId + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + lineColor + '" stop-opacity="0.26"/>' +
      '<stop offset="100%" stop-color="' + lineColor + '" stop-opacity="0.01"/>' +
      '</linearGradient></defs>' +
      ticks + volTicks +
      '<polygon points="' + areaPts.join(' ') + '" fill="url(#' + gradId + ')"/>' +
      bars +
      '<polyline points="' + linePts.join(' ') + '" fill="none" stroke="' + lineColor +
      '" stroke-width="1.5" stroke-linejoin="round"/>' +
      '<line x1="' + PAD_L + '" y1="' + (volTop - 5) + '" x2="' + (W - PAD_R) + '" y2="' + (volTop - 5) +
      '" stroke="#1e293b" stroke-width="1"/>' +
      '<text x="' + PAD_L + '" y="' + (volTop + 9) +
      '" fill="#475569" font-size="9" font-family="Inter, sans-serif">成交额(亿元)</text>' +
      xLabels;

    var vline = document.createElementNS(svgNS, 'line');
    vline.setAttribute('stroke', '#475569');
    vline.setAttribute('stroke-width', '1');
    vline.setAttribute('stroke-dasharray', '3 3');
    vline.setAttribute('y1', PAD_T);
    vline.setAttribute('y2', volTop + VOL_H);
    vline.style.display = 'none';
    svg.appendChild(vline);

    var dot = document.createElementNS(svgNS, 'circle');
    dot.setAttribute('r', '3.2');
    dot.setAttribute('fill', lineColor);
    dot.setAttribute('stroke', '#0b0f19');
    dot.setAttribute('stroke-width', '1.5');
    dot.style.display = 'none';
    svg.appendChild(dot);

    host.appendChild(svg);

    var tip = el('div', 'tooltip');
    host.appendChild(tip);

    function hide() {
      tip.style.opacity = '0';
      vline.style.display = 'none';
      dot.style.display = 'none';
    }

    svg.addEventListener('mousemove', function (ev) {
      var rect = svg.getBoundingClientRect();
      var relX = (ev.clientX - rect.left) / rect.width * W;
      if (relX < PAD_L || relX > W - PAD_R) { hide(); return; }
      var i = Math.round((relX - PAD_L) / plotW * (dates.length - 1));
      i = Math.max(0, Math.min(dates.length - 1, i));
      var px = x(i), py = y(closes[i]);
      vline.setAttribute('x1', px); vline.setAttribute('x2', px);
      vline.style.display = '';
      dot.setAttribute('cx', px); dot.setAttribute('cy', py);
      dot.style.display = '';

      var prev = i > 0 ? closes[i - 1] : closes[i];
      var chg = prev ? (closes[i] / prev - 1) * 100 : 0;
      var ds = String(dates[i]);
      var amt = amts[i];
      tip.innerHTML =
        '<div class="tt-date">' + ds.slice(0, 4) + '-' + ds.slice(4, 6) + '-' + ds.slice(6, 8) + '</div>' +
        '<div class="tt-row"><span class="tt-k">收盘</span><span>' + closes[i].toFixed(2) + '</span></div>' +
        '<div class="tt-row"><span class="tt-k">涨跌</span><span style="color:' +
        (chg >= 0 ? '#10b981' : '#ef4444') + '">' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%</span></div>' +
        ((amt === null || amt === undefined) ? '' :
          '<div class="tt-row"><span class="tt-k">成交额</span><span>' + Number(amt).toFixed(1) + ' 亿</span></div>');
      tip.style.opacity = '1';
      var left = px / W * rect.width + 14;
      if (left + 165 > rect.width) left = px / W * rect.width - 155;
      tip.style.left = Math.max(0, left) + 'px';
      tip.style.top = Math.max(0, py / totalH * rect.height - 18) + 'px';
    });
    svg.addEventListener('mouseleave', hide);
  }

  /* ------------------------------------------------------- 行业详情页 */
  function renderIndustry(code) {
    var host = $('#view-industry');
    var ind = (state.latest.industries || []).filter(function (x) { return x.code === code; })[0];
    if (!ind) { host.innerHTML = '<div class="empty">未找到该行业</div>'; return; }

    $('#page-title').textContent = ind.name;
    $('#page-sub').textContent = '申万一级行业 · ' + code;
    setActiveNav(code);
    host.innerHTML = '<div class="loading">加载中…</div>';

    loadJSON('history/' + code + '.json').then(function (hist) {
      host.innerHTML = '';

      var head = el('div', 'ind-head');
      head.innerHTML = '<h2>' + esc(ind.name) + '</h2><span class="code">' + esc(code) + '</span>' +
        '<span class="spacer"></span>';
      host.appendChild(head);

      // 走势图卡片
      var chartCard = el('div', 'card');
      chartCard.innerHTML = '<div class="card-title">行情走势 ' +
        '<span class="tag" style="background:var(--accent-dim);color:#60a5fa">PRICE + VOLUME</span></div>';
      var rangeBar = el('div', 'range-bar');
      [[1, '1Y'], [3, '3Y'], [5, '5Y'], [10, '10Y']].forEach(function (r) {
        var b = el('button', 'range-btn' + (state.range === r[0] ? ' active' : ''), r[1]);
        b.addEventListener('click', function () {
          state.range = r[0];
          renderIndustry(code);
        });
        rangeBar.appendChild(b);
      });
      chartCard.appendChild(rangeBar);
      var chartHost = el('div', 'chart-host');
      chartCard.appendChild(chartHost);
      host.appendChild(chartCard);
      renderChart(chartHost, hist);

      // 估值 / 景气
      host.appendChild(metricCard(ind));

      // ETF + 资讯
      var twoCol = el('div', 'two-col');
      twoCol.appendChild(etfCard(code));
      twoCol.appendChild(newsCard(code));
      host.appendChild(twoCol);
    }).catch(function (e) {
      host.innerHTML = '<div class="empty">历史数据加载失败:' + esc(e.message) + '</div>';
    });
  }

  function metricCard(ind) {
    var card = el('div', 'card');
    card.innerHTML = '<div class="card-title">估值 · 景气</div>';

    var grid = el('div', 'metric-grid');

    function cell(m) {
      var c = el('div', 'metric');
      var style = '';
      if (m.boxV !== undefined) {
        style = 'color:' + (m.boxV > 0 ? 'var(--green)' : (m.boxV < 0 ? 'var(--red)' : 'inherit'));
      }
      var shown = (m.value === null) ? '--' : (m.value + (m.suffix || ''));
      c.innerHTML = '<div class="metric-label">' + esc(m.label) + '</div>' +
        '<div class="metric-value" style="' + style + '">' + esc(shown) + '</div>' +
        '<div class="metric-sub">' + esc(m.sub) + '</div>';
      if (m.pbarV !== undefined) {
        var pb = el('div');
        pb.style.marginTop = '7px';
        pb.innerHTML = pbar(m.pbarV, m.invert);
        c.appendChild(pb);
      }
      return c;
    }

    // 第一行(5 列):PE / ROE / ROE同比 / ROE_Cap / ROE_Cap同比
    [
      { label: 'PE RATIO (TTM)', value: num(ind.pe, 2), suffix: 'x', sub: '估值水平' },
      { label: 'ROE (TTM)', value: num(ind.roe, 2), suffix: '%', sub: '盈利水平' },
      { label: 'ROE 同比', value: signed(ind.roeYoY, 2), suffix: '%', sub: '盈利趋势', boxV: ind.roeYoY },
      { label: 'ROE_CAP (TTM)', value: num(ind.roeCap, 2), suffix: '%', sub: '市值加权盈利' },
      { label: 'ROE_CAP 同比', value: signed(ind.roeCapYoY, 2), suffix: '%', sub: '市值加权趋势', boxV: ind.roeCapYoY }
    ].forEach(function (m) { grid.appendChild(cell(m)); });

    // 第二行:分位数方块与第一行上下对齐(PE→列1、ROE→列2、ROE_Cap→列4)
    [
      { col: 1, label: 'PE 分位数 (5Y)', pct: ind.pePct, invert: false, sub: '越高越贵' },
      { col: 2, label: 'ROE 分位数 (5Y)', pct: ind.roePct, invert: true, sub: '越高越景气' },
      { col: 4, label: 'ROE_CAP 分位数 (5Y)', pct: ind.roeCapPct, invert: true, sub: '越高越景气' }
    ].forEach(function (it) {
      var c = cell({
        label: it.label, value: num(it.pct, 1), sub: it.sub,
        pbarV: it.pct, invert: it.invert
      });
      c.classList.add('metric-pct', 'mcol-' + it.col);
      grid.appendChild(c);
    });

    card.appendChild(grid);
    return card;
  }

  function etfCard(code) {
    var card = el('div', 'card');
    var list = (state.etf && state.etf.industries && state.etf.industries[code]) || [];
    card.innerHTML = '<div class="card-title">相关 ETF ' +
      '<span class="tag" style="background:var(--green-dim);color:var(--green)">' + list.length + ' 只</span></div>';
    if (!list.length) {
      card.innerHTML += '<div class="empty">暂无匹配的 ETF</div>';
      return card;
    }
    var table = el('table', 'mini');
    table.innerHTML = '<thead><tr><th>代码</th><th>名称</th><th class="num">最新价</th>' +
      '<th class="num">涨跌幅</th><th class="num">成交额(亿)</th></tr></thead>';
    var tb = el('tbody');
    list.forEach(function (e) {
      var tr = el('tr');
      var chgColor = e.chg > 0 ? 'var(--green)' : (e.chg < 0 ? 'var(--red)' : 'var(--text-secondary)');
      tr.innerHTML = '<td class="etf-code">' + esc(e.code) + '</td>' +
        '<td>' + esc(e.name) + '</td>' +
        '<td class="num">' + (num(e.price, 3) || '--') + '</td>' +
        '<td class="num" style="color:' + chgColor + '">' + ((signed(e.chg, 2) || '--') + '%') + '</td>' +
        '<td class="num">' + (e.amount ? (e.amount / 1e8).toFixed(2) : '--') + '</td>';
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    card.appendChild(table);
    return card;
  }

  function newsCard(code) {
    var card = el('div', 'card');
    var list = (state.news && state.news.industries && state.news.industries[code]) || [];
    card.innerHTML = '<div class="card-title">相关资讯 ' +
      '<span class="tag" style="background:var(--accent-dim);color:#60a5fa">' + list.length + ' 条</span></div>';
    if (!list.length) {
      card.innerHTML += '<div class="empty">暂无相关资讯</div>';
      return card;
    }
    var host = el('div', 'news-list');
    list.slice(0, 10).forEach(function (n) {
      var item = el('div', 'news-item');
      var time = String(n.time || '').replace(/^\d{4}-\d{2}-/, '').slice(0, 11);
      var titleHtml = n.url
        ? '<a class="news-title" href="' + esc(n.url) + '" target="_blank" rel="noopener">' + esc(n.title) + '</a>'
        : '<span class="news-title">' + esc(n.title) + '</span>';
      item.innerHTML = '<div class="news-time">' + esc(time) + '</div>' +
        '<div class="news-body">' + titleHtml +
        '<span class="news-src">' + esc(n.source || '') + '</span>' +
        (n.summary ? '<div class="news-summary">' + esc(String(n.summary).slice(0, 110)) + '…</div>' : '') +
        '</div>';
      host.appendChild(item);
    });
    card.appendChild(host);
    return card;
  }

  /* ------------------------------------------------------------ 日期选择器 */
  function dayLabel(ds) { return state.labels[ds] || ds || ''; }

  function buildDatePicker() {
    var sel = $('#date-picker');
    if (!sel) return;
    sel.innerHTML = '';
    state.dates.forEach(function (ds) {
      var o = el('option');
      o.value = ds;
      o.textContent = dayLabel(ds);
      sel.appendChild(o);
    });
    sel.value = state.day || '';
  }

  function switchDate(ds) {
    if (!ds || ds === state.day) return;
    loadJSON('daily/' + ds + '.json').then(function (payload) {
      state.day = ds;
      state.latest = payload;
      delete $('#view-home').dataset.ready;      // 强制重建大表
      $('#foot-updated').textContent = '数据 ' + dayLabel(ds);
      document.title = 'Industry-Insight · ' + dayLabel(ds);
      if (state.current) renderIndustry(state.current);   // 行业页指标卡同步切换
      else renderHomeView();
    }, function (e) {
      // 失败要看得见:静默回滚会让人以为"选了没反应"
      if ($('#date-picker')) $('#date-picker').value = state.day;
      if ($('#foot-updated')) {
        $('#foot-updated').textContent =
          '数据 ' + dayLabel(state.day) + ' · 切换失败:' + (e && e.message ? e.message : '未知错误');
      }
    });
  }

  /* ------------------------------------------------------------ 视图切换 */
  function renderHomeView() {
    $('#page-title').textContent = '行业总览';
    $('#page-sub').textContent = '估值 · 拥挤 · 景气 · 动量 — 申万一级行业';
    setActiveNav('');
    if (!$('#view-home').dataset.ready) {
      renderHome();
      $('#view-home').dataset.ready = '1';
    }
    $('#view-home').classList.remove('hidden');
    $('#view-industry').classList.add('hidden');
    state.current = null;
  }

  function route() {
    var hash = location.hash.replace(/^#\/?/, '');
    if (!hash) { renderHomeView(); return; }
    $('#view-home').classList.add('hidden');
    $('#view-industry').classList.remove('hidden');
    setActiveNav(hash);
    renderIndustry(hash);
    state.current = hash;
  }

  /* -------------------------------------------------------------- 启动 */
  function boot() {
    Promise.all([
      loadJSON('latest.json'),
      loadJSON('etf.json').catch(function () { return { industries: {} }; }),
      loadJSON('news.json').catch(function () { return { industries: {} }; }),
      loadJSON('dates.json').catch(function () { return { dates: [], labels: {} }; })
    ]).then(function (res) {
      state.latest = res[0];
      state.etf = res[1];
      state.news = res[2];
      state.dates = (res[3].dates || []).slice();
      state.labels = res[3].labels || {};
      state.day = res[3].latest || state.dates[state.dates.length - 1] || '';

      // 兜底:没有 dates.json 时,至少让选择器里有最新一日
      if (!state.dates.length && state.latest.date) {
        var only = String(state.latest.date).replace(/-/g, '');
        state.dates = [only];
        state.labels[only] = state.latest.date;
        state.day = only;
      }

      var d = dayLabel(state.day) || state.latest.date || '';
      buildDatePicker();
      $('#foot-updated').textContent = '数据 ' + d;
      document.title = 'Industry-Insight · ' + d;
      buildNav(state.latest.industries);
      renderHomeView();
      window.addEventListener('hashchange', route);
      if (location.hash) route();
    }).catch(function (e) {
      $('#view-home').innerHTML = '<div class="empty">数据加载失败:' + esc(e.message) +
        '<br><br>请确认 docs/data/latest.json 已生成(运行 python export_dashboard.py)</div>';
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
