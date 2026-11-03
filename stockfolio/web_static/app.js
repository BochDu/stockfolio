(() => {
  'use strict';

  const DASH = '—';
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const COLORS = ['#4ad7ff', '#e7c15a', '#3de6a6', '#7aa2ff', '#e7a67a', '#c792ea'];
  const STATUS_CLASS = {
    ok: 'ok',
    busy: 'busy',
    noquote: 'bad',
    error: 'bad',
    initial: 'muted',
    fetched: 'muted',
    watch: 'muted',
  };

  const state = { payload: null, lang: 'zh', busy: false, category: null };

  const $ = (id) => document.getElementById(id);

  // 会话过期回到登录页时不丢当前语言；首次进入无显式语言时交给服务端默认
  function loginUrl(explicitLang) {
    const lang = explicitLang || (state.payload && state.payload.lang) || '';
    return lang ? `/login?lang=${encodeURIComponent(lang)}` : '/login';
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function svg(tag) {
    return document.createElementNS(SVG_NS, tag);
  }

  // ---------- 数字格式：w/k 单位、价格、数量、百分比 ----------

  function trim(value) {
    return value.replace(/0+$/, '').replace(/\.$/, '');
  }

  function toDisplay(value) {
    return state.lang === 'en' ? value * 10 : value;
  }

  function fmtW(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return DASH;
    const text = trim(toDisplay(Number(value)).toFixed(2));
    return text === '' ? '0' : text;
  }

  function fmtPx(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return DASH;
    const text = trim(Number(value).toFixed(4));
    return text === '' ? '0' : text;
  }

  function fmtQty(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return DASH;
    const number = Number(value);
    return Number.isInteger(number) ? String(number) : trim(number.toFixed(4));
  }

  function fmtPct(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return DASH;
    return `${trim(Number(value).toFixed(2))}%`;
  }

  function fmtHeat(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return DASH;
    return Number(value).toFixed(1);
  }

  function heatClass(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'muted';
    const heat = Number(value);
    if (heat >= 80) return 'heat-hot';
    if (heat >= 60) return 'heat-warm';
    if (heat >= 40) return 'heat-neutral';
    if (heat >= 20) return 'heat-cool';
    return 'heat-ice';
  }

  function actionClass(text) {
    if (!text) return 'muted';
    if (text.startsWith('加仓') || text.startsWith('Add')) return 'action-add';
    if (text.startsWith('减仓') || text.startsWith('Reduce')) return 'action-reduce';
    return 'muted';
  }

  function joinNames(values, lang) {
    return values.join(lang === 'en' ? ', ' : '、');
  }

  // ---------- 摘要 ----------

  function card(label, value, strip, valueClass) {
    const box = el('div', 'card');
    box.append(el('div', `strip ${strip || ''}`));
    box.append(el('div', 'label', label));
    box.append(el('div', `value ${valueClass || ''}`, value));
    return box;
  }

  function renderSummary(payload) {
    const box = $('summary-row');
    box.innerHTML = '';
    const L = payload.labels;
    const summary = payload.summary;

    if (!summary) {
      box.className = 'summary-row single';
      // 没有持仓（纯观察）不等于价格没就绪
      const text =
        payload.status.key === 'watch'
          ? payload.status.text
          : L['价格/汇率未就绪'];
      box.append(card(L['摘要'], text, 'strip-gold'));
      return;
    }

    box.className = 'summary-row';
    const unit = payload.unit;
    const pnl = summary['整体盈亏pct'];
    const cashPct = payload.view['当前现金pct'];
    const heat = payload.view['温度'];

    const quota = summary['目标总仓'];
    box.append(
      card(
        L['目标总仓'],
        quota === null || quota === undefined ? DASH : `${fmtW(quota)} ${unit}`,
        'strip-gold'
      )
    );
    box.append(card(L['当前总仓'], `${fmtW(summary['当前总仓_w'])} ${unit}`, 'strip-cyan'));
    box.append(card(L['当前现金'], `${fmtW(summary['当前现金_w'])} ${unit}`, 'strip-cyan'));
    const cashTone = heatClass(heat);
    box.append(
      card(
        L['现金占比'],
        cashPct === null || cashPct === undefined ? DASH : `${Number(cashPct).toFixed(1)}%`,
        cashTone,
        cashTone
      )
    );
    const pnlTone =
      pnl === null || pnl === undefined ? 'muted' : pnl >= 0 ? 'gain' : 'loss';
    box.append(card(L['整体盈亏'], fmtPct(pnl), pnlTone, pnlTone));

    const skipped = summary['现金跳过'] || [];
    if (skipped.length) {
      box.append(el('p', 'note', L['现金未折算：'] + joinNames(skipped, payload.lang)));
    }
    if (Number(summary['报价失败']) > 0) {
      box.append(
        el(
          'p',
          'note',
          L['报价不全：部分持仓未取到价格，市值、现金占比与平衡建议仅按已取价部分计算。']
        )
      );
    }
  }

  // ---------- 估值 / 温度 ----------

  function panelHead(title, extra) {
    const head = el('div', 'panel-head');
    head.append(el('span', 'panel-title', title));
    if (extra) head.append(extra);
    return head;
  }

  function renderValuation(payload) {
    const row = $('valuation-row');
    row.innerHTML = '';
    const L = payload.labels;
    const view = payload.view;

    const market = el('section', 'panel');
    market.append(panelHead(L['市场温度']));
    for (const [name, heatText, reading] of view['参考行'] || []) {
      const line = el('div', 'ref-row');
      line.append(el('span', 'ref-name', name));
      const heat = heatText === DASH ? null : Number(heatText);
      line.append(el('span', `ref-value ${heatClass(heat)}`, `${heatText} · ${reading}`));
      market.append(line);
    }
    market.append(
      el('p', 'footnote', L['三条大盘只做参考，不参与温度计算（0=冰点 100=泡沫）'])
    );
    row.append(market);

    const holding = el('section', 'panel');
    const head = panelHead(L['持仓温度']);
    holding.append(head);
    const plan = view['建议'];
    if (!plan) {
      const watchOnly = payload.status.key === 'watch';
      head.append(
        el(
          'span',
          'panel-hint',
          watchOnly ? payload.status.text : L['点击「刷新」拉取估值数据']
        )
      );
      if (!watchOnly) {
        holding.append(
          el('p', 'panel-hint', L['由每只持仓自己的逆向指标按市值加权合成'])
        );
      }
      row.append(holding);
      return;
    }

    const lines = [];
    lines.push([
      L['持仓温度'],
      `${Number(plan['温度']).toFixed(1)} / 100 · ${payload.heat_reading}`,
      heatClass(view['温度']),
    ]);
    lines.push([L['判定档位'], plan['档位'], '']);
    let cashLabel = `${Number(plan['现金']).toFixed(1)}%`;
    if (view['当前现金pct'] !== null && view['当前现金pct'] !== undefined) {
      cashLabel = `${Number(view['当前现金pct']).toFixed(1)}% → ${Number(plan['现金']).toFixed(1)}%`;
    }
    lines.push([L['现金建议'], cashLabel, '']);
    if (view['操作']) {
      lines.push([L['合理操作'], String(view['操作']), actionClass(String(view['操作']))]);
    }
    const skipped = view['跳过'] || [];
    if (skipped.length) {
      lines.push([L['无足够历史'], joinNames(skipped, payload.lang), 'muted']);
    }

    for (const [label, value, cls] of lines) {
      const line = el('div', 'kv-row');
      line.append(el('span', 'kv-label', label));
      line.append(el('span', `kv-value ${cls || ''}`, value));
      holding.append(line);
    }
    row.append(holding);
  }

  // ---------- 饼图 ----------

  function drawPie(wrap, slices, emptyText) {
    wrap.innerHTML = '';
    const usable = (slices || []).filter((item) => Number(item.value) > 0);
    if (!usable.length) {
      wrap.append(el('p', 'panel-hint', emptyText));
      return;
    }

    const total = usable.reduce((sum, item) => sum + Number(item.value), 0);
    const size = 180;
    const cx = 90;
    const cy = 90;
    const radius = 74;
    const chart = svg('svg');
    chart.setAttribute('viewBox', `0 0 ${size} ${size}`);
    chart.setAttribute('width', String(size));
    chart.setAttribute('height', String(size));
    chart.setAttribute('class', 'pie-svg');
    chart.setAttribute('role', 'img');

    let start = -Math.PI / 2;
    usable.forEach((item, index) => {
      const color = COLORS[index % COLORS.length];
      let node;
      if (usable.length === 1) {
        node = svg('circle');
        node.setAttribute('cx', String(cx));
        node.setAttribute('cy', String(cy));
        node.setAttribute('r', String(radius));
        node.setAttribute('fill', color);
      } else {
        const end = start + (Number(item.value) / total) * Math.PI * 2;
        const large = end - start > Math.PI ? 1 : 0;
        const x0 = cx + radius * Math.cos(start);
        const y0 = cy + radius * Math.sin(start);
        const x1 = cx + radius * Math.cos(end);
        const y1 = cy + radius * Math.sin(end);
        node = svg('path');
        node.setAttribute(
          'd',
          `M ${cx} ${cy} L ${x0.toFixed(3)} ${y0.toFixed(3)} ` +
            `A ${radius} ${radius} 0 ${large} 1 ${x1.toFixed(3)} ${y1.toFixed(3)} Z`
        );
        node.setAttribute('fill', color);
        start = end;
      }
      node.setAttribute('stroke', '#070b14');
      node.setAttribute('stroke-width', '1');
      chart.append(node);
    });

    const legend = el('div', 'pie-legend');
    usable.forEach((item, index) => {
      const line = el('div', 'legend-row');
      line.append(el('i', `swatch c${index % 6}`));
      const pct = (Number(item.value) / total) * 100;
      line.append(el('span', '', `${item.label}  ${pct.toFixed(1)}%`));
      legend.append(line);
    });

    wrap.append(chart, legend);
  }

  function renderCharts(payload) {
    const row = $('charts-row');
    row.innerHTML = '';
    const L = payload.labels;
    const summary = payload.summary;

    const left = el('section', 'panel');
    left.append(panelHead(L['大类占比']));
    const catWrap = el('div', 'pie-wrap');
    const catSlices = summary
      ? (summary['大类'] || []).map((item) => ({
          label: item['名称'],
          value: Number(item['当前仓']),
        }))
      : [];
    drawPie(catWrap, catSlices, L['暂无占比']);
    left.append(catWrap);
    row.append(left);

    const right = el('section', 'panel');
    const head = panelHead(L['标的占比']);
    const names = (payload.book['大类'] || []).map((cat) => cat['名称']);

    const select = document.createElement('select');
    select.setAttribute('aria-label', L['标的占比']);
    for (const name of names) {
      const option = el('option', '', name);
      option.value = name;
      select.append(option);
    }

    let preferred = names[0] || '';
    for (const name of names) {
      const hasValue = (payload.enriched || []).some(
        (row) =>
          row['大类'] === name && row['可汇总'] && Number(row['市值_本位']) > 0
      );
      if (hasValue) {
        preferred = name;
        break;
      }
    }
    if (!names.includes(state.category)) state.category = preferred;
    select.value = state.category || preferred;
    head.append(select);
    right.append(head);

    const holdingWrap = el('div', 'pie-wrap');
    right.append(holdingWrap);
    row.append(right);

    const updateHolding = () => {
      const slices = (payload.enriched || [])
        .filter((row) => row['大类'] === state.category && row['可汇总'])
        .map((row) => ({ label: row['标的'], value: Number(row['市值_本位']) }));
      drawPie(holdingWrap, slices, L['暂无占比']);
    };

    select.addEventListener('change', () => {
      state.category = select.value;
      updateHolding();
    });
    updateHolding();
  }

  // ---------- 分类明细 ----------

  function renderCategories(payload) {
    const box = $('categories');
    box.innerHTML = '';
    const L = payload.labels;
    const summary = payload.summary;
    const view = payload.view;

    const meta = {};
    if (summary) {
      for (const item of summary['大类'] || []) meta[item['名称']] = item;
    }
    const heatByName = {};
    for (const item of view['大类热度'] || []) heatByName[item['名称']] = item;
    const heatByCode = view['代码热度'] || {};

    for (const cat of payload.book['大类'] || []) {
      const name = cat['名称'];
      const ratio = Number(cat['比例']);
      const panel = el('section', 'cat-panel');

      const head = el('div', 'cat-head');
      head.append(el('span', 'cat-name', `${name}  ·  ${Math.round(ratio * 100)}%`));

      const info = meta[name];
      const labels = [L['目标'], L['持仓'], L['占比'], L['平衡']];
      let detail;
      let detailClass = 'muted';
      if (!info) {
        detail = labels.map((label) => `${label} ${DASH}`).join('    ');
      } else {
        const unit = payload.unit;
        const target = info['目标仓'];
        const share = info['当前占比'];
        const balance = info['平衡'];
        const targetText =
          target === null || target === undefined ? DASH : `${fmtW(target)} ${unit}`;
        const shareText =
          share === null || share === undefined
            ? DASH
            : `${Number(share).toFixed(1)}%`;
        const balanceText =
          balance === null || balance === undefined
            ? DASH
            : `${Number(balance) > 0 ? '+' : ''}${toDisplay(Number(balance)).toFixed(1)} ${unit}`;
        detail =
          `${labels[0]} ${targetText}    ` +
          `${labels[1]} ${fmtW(info['当前仓'])} ${unit}    ` +
          `${labels[2]} ${shareText}    ` +
          `${labels[3]} ${balanceText}`;
        detailClass =
          balance === null || balance === undefined
            ? 'muted'
            : balance > 0
              ? 'action-add'
              : balance < 0
                ? 'action-reduce'
                : 'muted';
      }
      head.append(el('span', `cat-detail ${detailClass}`, detail));
      panel.append(head);

      // 没有温度信息的大类也保留这一行的高度，表头不会顶到标题上
      const heatInfo = heatByName[name];
      const sub = el('div', heatInfo ? 'cat-sub' : 'cat-sub empty');
      if (heatInfo) {
        const heat = heatInfo['热度'];
        sub.append(
          el(
            'span',
            heatClass(heat),
            heat === null || heat === undefined
              ? `${L['温度']} —`
              : `${L['温度']} ${Number(heat).toFixed(1)} · ${heatInfo['读数']}`
          )
        );
        const coverage =
          payload.lang === 'en'
            ? `Coverage ${heatInfo['样本']}`
            : `覆盖 ${heatInfo['样本']} 只`;
        sub.append(el('span', 'muted', coverage));
      } else {
        sub.append(el('span', 'muted', '\u00a0'));
      }
      panel.append(sub);

      const rows = (payload.enriched || []).filter((row) => row['大类'] === name);
      if (!rows.length) {
        panel.append(el('p', 'panel-hint', L['无持仓']));
        box.append(panel);
        continue;
      }

      const classTotal = rows
        .filter((row) => row['可汇总'])
        .reduce((sum, row) => sum + Number(row['市值_本位'] || 0), 0);

      const tableWrap = el('div', 'table-wrap');
      const table = el('table');
      const thead = el('thead');
      const headRow = el('tr');
      const headings = [
        L['标的'],
        L['货币'],
        L['数量'],
        L['成本价'],
        L['现价'],
        L['市值(w)'],
        L['占大类%'],
        L['盈亏%'],
        L['温度'],
      ];
      for (const heading of headings) headRow.append(el('th', '', heading));
      thead.append(headRow);
      table.append(thead);

      const tbody = el('tbody');
      for (const row of rows) {
        const quoted = Boolean(row['可汇总']);
        const heat = heatByCode[String(row['代码'])];
        const heatText = heat === null || heat === undefined ? DASH : Number(heat).toFixed(1);

        let share = DASH;
        let value = DASH;
        let pnl = DASH;
        let pnlClass = 'muted';
        if (quoted) {
          share =
            classTotal > 0
              ? `${((Number(row['市值_本位']) / classTotal) * 100).toFixed(1)}%`
              : DASH;
          value = fmtW(Number(row['市值_本位']) / 10000);
          const pnlValue = row['盈亏pct'];
          pnl = fmtPct(pnlValue);
          pnlClass =
            pnlValue === null || pnlValue === undefined
              ? 'muted'
              : pnlValue >= 0
                ? 'gain'
                : 'loss';
        }

        // 观察仓（数量 0）：现价和温度照样展示，只有市值/占比/盈亏是空的
        const tr = el('tr');
        const tone = quoted ? '' : 'muted';
        tr.append(el('td', tone, row['标的']));
        tr.append(el('td', tone, row['货币']));
        tr.append(el('td', tone, fmtQty(row['数量'])));
        tr.append(el('td', tone, fmtPx(row['成本价'])));
        tr.append(el('td', tone, fmtPx(row['解析现价'])));
        tr.append(el('td', tone, value));
        tr.append(el('td', tone, share));
        tr.append(el('td', pnlClass, pnl));
        tr.append(
          el(
            'td',
            heat === null || heat === undefined ? 'muted' : heatClass(heat),
            heatText
          )
        );
        tbody.append(tr);
      }
      table.append(tbody);
      tableWrap.append(table);
      panel.append(tableWrap);
      box.append(panel);
    }
  }

  // ---------- 整体渲染 ----------

  function render() {
    const payload = state.payload;
    if (!payload) return;
    state.lang = payload.lang;
    document.documentElement.lang = payload.lang === 'en' ? 'en' : 'zh-CN';
    document.title = `${payload.title} · ${payload.labels['本位币']} ${payload.base}`;

    $('page-title').textContent = payload.title;
    const status = $('status');
    status.textContent = payload.status.text;
    status.className = `status ${STATUS_CLASS[payload.status.key] || 'muted'}`;

    $('refresh-btn').textContent = payload.labels['刷新'];
    $('lang-btn').textContent = payload.labels['EN'];
    $('logout-btn').textContent = payload.lang === 'en' ? 'Sign out' : '退出';
    $('logout-btn').form.action = `/logout?lang=${payload.lang}`;
    $('logout-btn').closest('form').classList.toggle('hidden', !payload.auth);
    $('refresh-btn').disabled = state.busy || Boolean(payload.refreshing);

    renderSummary(payload);
    renderValuation(payload);
    renderCharts(payload);
    renderCategories(payload);
  }

  function showNotice(message) {
    const notice = $('notice');
    notice.textContent = message;
    notice.classList.remove('hidden');
    clearTimeout(showNotice.timer);
    showNotice.timer = setTimeout(() => notice.classList.add('hidden'), 5000);
  }

  async function getState(lang) {
    const suffix = lang ? `?lang=${encodeURIComponent(lang)}` : '';
    const response = await fetch(`/api/state${suffix}`, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    if (response.status === 401) {
      window.location.href = loginUrl(lang);
      throw new Error('登录已过期');
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function setBusyUi(busy) {
    $('refresh-btn').disabled = busy;
    if (busy && state.payload) {
      const status = $('status');
      status.textContent = state.payload.labels['正在拉取报价…'];
      status.className = 'status busy';
    }
  }

  async function applyPayload(payload) {
    // 刷新期间用户可能切换语言：旧语言响应不能把界面切回去
    if (state.payload && payload.lang !== state.lang) {
      payload = await getState(state.lang);
    }
    state.payload = payload;
    render();
  }

  async function poll() {
    for (let i = 0; i < 120; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await applyPayload(await getState(state.lang));
      if (!state.payload.refreshing) return;
    }
    throw new Error('刷新超时，请稍后重试');
  }

  async function doRefresh() {
    if (state.busy) return;
    state.busy = true;
    setBusyUi(true);
    try {
      const response = await fetch(`/api/refresh?lang=${encodeURIComponent(state.lang)}`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'X-Stockfolio': '1' },
        cache: 'no-store',
      });
      if (response.status === 401) {
        window.location.href = loginUrl();
        return;
      }
      if (response.status === 202) {
        await poll();
        return;
      }
      if (!response.ok) {
        let message = `HTTP ${response.status}`;
        try {
          const body = await response.json();
          if (body && body.error) message = body.error;
        } catch (error) {
          /* 非 JSON 错误响应，保留状态码 */
        }
        throw new Error(message);
      }
      await applyPayload(await response.json());
    } catch (error) {
      showNotice(error.message || '刷新失败');
    } finally {
      state.busy = false;
      setBusyUi(false);
      render();
    }
  }

  async function toggleLang() {
    const target = state.lang === 'en' ? 'zh' : 'en';
    state.payload = await getState(target);
    const url = new URL(window.location.href);
    url.searchParams.set('lang', target);
    window.history.replaceState(null, '', url);
    render();
  }

  $('refresh-btn').addEventListener('click', doRefresh);
  $('lang-btn').addEventListener('click', () => {
    toggleLang().catch((error) => showNotice(error.message || '切换语言失败'));
  });

  (async () => {
    try {
      // 登录页明确选择的语言经 ?lang= 带入；没有参数时仍按持仓默认语言
      const initial = new URLSearchParams(window.location.search).get('lang');
      state.payload = await getState(initial === 'en' || initial === 'zh' ? initial : undefined);
      render();
      if (state.payload.status.key === 'initial') await doRefresh();
    } catch (error) {
      showNotice(error.message || '加载失败');
    }
  })();
})();
