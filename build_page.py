#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_page.py — 从本地证据源构建"常用表速查复制器"单页 HTML。

数据源优先级：
  1) hue_table_spider/output_api*/  DDL markdown（列中文注释最全）
  2) picosql-resource-pack/registry/{tables,columns}.jsonl（注册表兜底）
  3) calibers.json 人工口径层（usage/grain/biz_date/joins/notes，缺失表可全手工）

用法: python3 build_page.py   → 生成 index.html（内嵌全部数据，双击即用）
"""
import json, re, os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPIDER = os.path.join(ROOT, "hue_table_spider")
REG = os.path.join(ROOT, "picosql-resource-pack", "registry")

# 分组与白名单（依据近 20 天会话 FROM/JOIN 频率，2026-09-23 回溯）
WHITELIST = {
    "房间绩效": ["app.new_room_statistics_state", "app.crm_room_performance_statistics",
               "app.crm_room_performance_statistics_weekly", "app.crm_room_anchor_earnings_detail"],
    "用户活跃与身份": ["dws.dws_user_identity_active_a_d", "dws.dws_user_statistics_info_a_d",
                  "dws.dws_user_behavior_milestone_a_d", "dws.dws_user_relation_state_a_d"],
    "送礼与充值": ["dwd.fact_yoy_user_gift_record_i_d", "dwd.fact_yoy_anchor_gift_record_i_d",
               "dws.dws_user_gift_recharge_a_d", "ods.ods_yoy_coin_change_record_a_d",
               "ods.ods_yoy_coin_summary_record_a_d", "app.new_register_recharge_analyse_state",
               "ods.ods_yoy_buy_goods_record_a_d", "dws.dws_user_guild_room_anchor_gift_i_d"],
    "主播与公会": ["app.anchor_daily_live_statistics_state", "dim.dim_anchor_info",
               "dwd.fact_t_guild_anchor_living_a_d", "ods.ods_flamingo_guild_public_payment_conf_a_d",
               "ods.ods_flamingo_anchor_public_payment_conf_a_d", "dim.dim_guild_info",
               "ods.ods_yoy_user_agreement_a_d"],
    "用户与进房": ["ods.ods_yoy_users_a_d", "ods.ods_flamingo_t_room_in_out_log_i_d",
               "ods.ods_yoy_recommend_room_a_d", "ods.ods_sensors_show_rooms_effectively_i_d",
               "app.newbie_conversion_info", "ods.ods_flamingo_t_room_a_d"],
    "内部账与提现": ["ods.ods_eagle_t_inner_accounts", "ods.ods_yoy_user_cash_out_apply_a_d"],
    "短信召回": ["ods.ods_yoy_sms_marketing_task_user_a_d", "ods.ods_yoy_sms_recall_reward_pack_a_d"],
    "MySQL-宝箱金币": ["yoy.box_draw_consume_record", "yoy.coin_change_record", "yoy.users",
                  "yoy.user_gift_record", "yoy.user_cumulative_recharge_record",
                  "yoy.user_cumulative_recharge", "yoy.item_detail", "yoy.biz_props", "yoy.biz_goods"],
}
# 白名单表 → spider md 的实际文件（表名与文档名不一致时在此映射）
MD_ALIAS = {
    "app.newbie_conversion_info": ["app/app_newbie_conversion_info.md"],
    "ods.ods_flamingo_guild_public_payment_conf_a_d": ["ods/ods_flamingo_guild_public_payment_conf_record_a_d.md"],
    "ods.ods_flamingo_anchor_public_payment_conf_a_d": ["ods/ods_flamingo_anchor_public_payment_conf_record_a_d.md"],
    "yoy.coin_change_record": ["yoy/coin_change_record.md"],
    "yoy.users": ["yoy/users.md"],
    "yoy.user_gift_record": ["yoy/user_gift_record.md"],
    "yoy.user_cumulative_recharge_record": ["yoy/user_cumulative_recharge_record.md"],
    "yoy.item_detail": ["yoy/item_detail.md"],
    "yoy.biz_props": ["yoy/biz_props.md"],
    "yoy.biz_goods": ["yoy/biz_goods.md"],
}
# box_draw_consume_record 未抓 DDL，列定义来自 memory 口径（Hue 已确认列）
MANUAL_COLS = {
    "yoy.box_draw_consume_record": [
        ("box_goods_id", "BIGINT", "宝箱商品ID（拆箱维度主键）"),
        ("draw_config_id", "BIGINT", "抽取配置ID"),
        ("goods_index", "BIGINT", "箱内位置索引"),
        ("gift_record_id", "BIGINT", "子送礼记录ID，对齐 user_gift_record.id"),
        ("gift_record_id_extends", "BIGINT", "父送礼记录ID（批量送礼）"),
        ("from_user_id", "BIGINT", "开箱用户"),
        ("to_user_id", "BIGINT", "收礼主播"),
        ("goods_id", "BIGINT", "开出的礼物"),
        ("goods_level", "INT", "礼物等级 3=传说 4=典藏"),
        ("config_goods_price", "BIGINT", "配置价值"),
        ("actual_goods_price", "BIGINT", "实际开出价值（用这个）"),
        ("consume_state", "TINYINT", "1=成功（只计这个）0=待确认 2=失败取消"),
        ("create_timestamp", "BIGINT", "毫秒时间戳，/1000 转 +08:00"),
        ("num", "INT", "批量数量（勿再乘到金额上）"),
    ],
}
COL_LIMIT = 18


def parse_md_ddl(path):
    """解析 spider md 里的 CREATE TABLE，返回 (columns, partition_cols, table_comment)。"""
    text = open(path, encoding="utf-8").read()
    cols, parts = [], []
    m = re.search(r"CREATE TABLE\s+[^\(]+\((.*?)\)\s*(?:PARTITIONED BY|\Z)", text, re.S)
    if not m:
        return cols, parts, ""
    body = m.group(1)
    # 去掉嵌套括号里的内容对 COMMENT 切分的影响：按行解析
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        cm = re.match(r"([a-z_][a-z_0-9]*)\s+(STRING|BIGINT|INT|TINYINT|SMALLINT|DECIMAL\([^)]*\)|DOUBLE|FLOAT|BOOLEAN|TIMESTAMP|DATE|VARCHAR\([^)]*\)|TEXT|JSON)\s*(?:COMMENT\s+'((?:[^']|'')*)')?", line, re.I)
        if cm:
            cols.append((cm.group(1), cm.group(2).upper(), (cm.group(3) or "").replace("''", "'")[:80]))
    pm = re.search(r"PARTITIONED BY\s*\((.*?)\)", text, re.S)
    if pm:
        parts = [x.strip().split()[0] for x in pm.group(1).split(",") if x.strip()]
    tc = re.search(r"COMMENT\s+'([^']*)'\s*\)?\s*$", text.rstrip())
    return cols, parts, (tc.group(1) if tc else "")


def main():
    calibers = json.load(open(os.path.join(HERE, "calibers.json"), encoding="utf-8"))
    rules = calibers.pop("_global_rules")
    calibers.pop("_meta", None)

    # 注册表
    reg_tables, reg_cols = {}, defaultdict(list)
    for line in open(os.path.join(REG, "tables.jsonl"), encoding="utf-8"):
        t = json.loads(line)
        reg_tables[f"{t['schema_name']}.{t['table_name']}"] = t
    for line in open(os.path.join(REG, "columns.jsonl"), encoding="utf-8"):
        c = json.loads(line)
        reg_cols[c["table_id"].replace("table::", "")].append(c)

    # 历史 SQL 列使用频率（关键列排序参考）
    coluse = Counter()
    for line in open(os.path.join(REG, "sql_assets.jsonl"), encoding="utf-8"):
        sql = json.loads(line).get("sql_text", "") or ""
        for tok in re.findall(r"\b([a-z_][a-z_0-9]{2,30})\b", sql.lower()):
            coluse[tok] += 1

    # spider md 索引
    md_index = {}
    for base in ("output_api", "output_api_mysql"):
        d = os.path.join(SPIDER, base)
        for sub in os.listdir(d) if os.path.isdir(d) else []:
            subd = os.path.join(d, sub)
            if os.path.isdir(subd):
                for f in os.listdir(subd):
                    if f.endswith(".md"):
                        md_index[f[:-3]] = os.path.join(subd, f)

    KEY = re.compile(r"^(user_id|anchor_id|room_id|guild_id|room_number|from_user_id|to_user_id|uid|id|dt|format_dt|format_date|stat_date|biz_date|create_time|create_timestamp|pay_coins|coin|amount|uv|src_type|goods_id|prop_id)")
    groups, stats = [], Counter()
    for grp, tables in WHITELIST.items():
        gtables = []
        for full in tables:
            cal = calibers.get(full, {})
            dialect = "mysql" if full.startswith("yoy.") or full.startswith("flamingo.") else "impala"
            # 1) md
            cols = []
            md_path = None
            for cand in MD_ALIAS.get(full, []) + [full.replace(".", "_", 1)]:
                if cand in md_index:
                    md_path = md_index[cand]; break
            if not md_path and full.split(".")[-1] in md_index:
                md_path = md_index[full.split(".")[-1]]
            if md_path:
                cols, parts, _ = parse_md_ddl(md_path)
                dialect_md = "mysql" if "/output_api_mysql/" in md_path else "impala"
                dialect = dialect_md
            # 2) 注册表兜底
            ddl_missing = False
            if not cols and full in reg_tables:
                for c in reg_cols[full]:
                    cols.append((c["name"], c["data_type"], (c.get("comment") or "")[:80]))
                dialect = reg_tables[full]["dialect"]
            if not cols and full in MANUAL_COLS:
                cols = list(MANUAL_COLS[full])
            if not cols:
                ddl_missing = True
                stats["no_ddl"] += 1
            partition = next((n for n, _, _ in cols if n == "dt"), None)
            # 关键列评分排序：join键/日期/度量优先，其次历史使用频率
            cols = sorted(dict.fromkeys(cols), key=lambda c: (
                0 if KEY.match(c[0]) else 1, -coluse.get(c[0], 0)))
            gtables.append({
                "n": full, "d": dialect,
                "u": cal.get("usage") or (reg_tables.get(full, {}).get("table_comment") or ""),
                "g": cal.get("grain", ""), "b": cal.get("biz_date", ""),
                "j": cal.get("joins", ""),
                "notes": cal.get("notes", []),
                "cols": [{"n": n, "t": t, "c": c} for n, t, c in cols[:COL_LIMIT]],
                "n_all": len(cols), "ddl_missing": ddl_missing,
            })
            stats["tables"] += 1
        groups.append({"name": grp, "tables": gtables})

    data = {"groups": groups, "rules": rules, "built": "2026-09-23"}
    json.dump(data, open(os.path.join(HERE, "tables_data.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__BUILD__", data["built"])
    out = os.path.join(HERE, "index.html")
    open(out, "w", encoding="utf-8").write(html)
    print(f"OK: {stats['tables']} tables, no_ddl={stats['no_ddl']} -> {out}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>常用表速查 · 一键复制给 AI</title>
<style>
  :root{
    --bg:#f5f6f8; --card:#fff; --ink:#1c2330; --sub:#6b7482; --line:#e4e7ec;
    --accent:#2f6fed; --accent-soft:#eaf1fe; --warn:#c0392b; --warn-bg:#fdf0ee;
    --mysql:#0f7b6c; --mysql-bg:#e6f4f1; --ok:#1e8e3e;
  }
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink)}
  header{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid var(--line);padding:10px 20px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
  header h1{font-size:16px;margin:0;white-space:nowrap}
  header .tip{color:var(--sub);font-size:12px}
  #search{flex:1;min-width:200px;padding:7px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px;outline:none}
  #search:focus{border-color:var(--accent)}
  .layout{display:flex;max-width:1280px;margin:0 auto;padding:16px 20px 120px;gap:18px}
  nav{width:158px;flex-shrink:0;position:sticky;top:64px;align-self:flex-start;max-height:calc(100vh - 90px);overflow:auto}
  nav a{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;border-radius:7px;color:var(--ink);text-decoration:none;font-size:13px;margin-bottom:2px}
  nav a:hover{background:var(--accent-soft)}
  nav a .cnt{color:var(--sub);font-size:11px}
  main{flex:1;min-width:0}
  .group-title{font-size:14px;font-weight:600;margin:22px 0 10px;display:flex;align-items:center;gap:10px}
  .group-title:first-child{margin-top:4px}
  .group-title .btn-sel{font-size:12px;font-weight:400;color:var(--accent);cursor:pointer;border:1px solid var(--accent);border-radius:6px;padding:1px 8px;background:#fff}
  .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;position:relative;transition:box-shadow .15s}
  .card.sel{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
  .card.hide{display:none}
  .card .row1{display:flex;align-items:flex-start;gap:8px}
  .card input[type=checkbox]{margin-top:4px;accent-color:var(--accent);width:15px;height:15px;cursor:pointer}
  .tname{font-weight:600;font-size:13.5px;word-break:break-all;font-family:ui-monospace,Menlo,monospace}
  .badge{font-size:10.5px;padding:1px 7px;border-radius:10px;font-weight:500;white-space:nowrap}
  .badge.impala{color:var(--accent);background:var(--accent-soft)}
  .badge.mysql{color:var(--mysql);background:var(--mysql-bg)}
  .badge.miss{color:var(--warn);background:var(--warn-bg)}
  .usage{color:var(--sub);font-size:12.5px;margin:4px 0 2px}
  .meta{font-size:12px;color:#445;margin-top:4px}
  .meta b{color:var(--sub);font-weight:500}
  details{margin-top:8px}
  summary{font-size:12.5px;color:var(--accent);cursor:pointer;user-select:none}
  table.cols{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
  table.cols td{border-top:1px solid var(--line);padding:3px 4px;vertical-align:top}
  table.cols td.n{font-family:ui-monospace,Menlo,monospace;white-space:nowrap;color:#153a6b}
  table.cols td.t{color:var(--sub);white-space:nowrap;font-size:11px}
  ul.notes{margin:6px 0 0;padding-left:18px;font-size:12px;color:#7a3020}
  ul.notes li{margin-bottom:3px}
  .dock{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);padding:10px 20px;display:flex;gap:12px;align-items:center;justify-content:center;z-index:30;box-shadow:0 -3px 14px rgba(0,0,0,.06)}
  .dock .selinfo{font-size:13px}
  .dock .selinfo b{color:var(--accent)}
  .btn{border:none;border-radius:8px;padding:9px 18px;font-size:13.5px;cursor:pointer}
  .btn.primary{background:var(--accent);color:#fff}
  .btn.primary:hover{background:#2559c4}
  .btn.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}
  .btn.ghost:hover{border-color:var(--accent);color:var(--accent)}
  #toast{position:fixed;bottom:76px;left:50%;transform:translateX(-50%) translateY(20px);background:#20303f;color:#fff;padding:9px 22px;border-radius:9px;font-size:13px;opacity:0;pointer-events:none;transition:all .25s;z-index:40}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  .empty{color:var(--sub);text-align:center;padding:40px 0}
  footer{max-width:1280px;margin:0 auto;padding:0 20px 20px;color:var(--sub);font-size:12px}
</style>
</head>
<body>
<header>
  <h1>📋 常用表速查</h1>
  <input id="search" type="search" placeholder="搜索表名 / 用途 / 列名 / 口径关键词…">
  <span class="tip">勾选本次需求涉及的表 → 复制 → 粘到新会话开场</span>
</header>
<div class="layout">
  <nav id="nav"></nav>
  <main id="main"></main>
</div>
<div class="dock">
  <span class="selinfo">已选 <b id="selcnt">0</b> 张表</span>
  <button class="btn primary" id="btnCopyFull">复制给 AI（完整口径）</button>
  <button class="btn ghost" id="btnCopyNames">仅复制表名清单</button>
  <button class="btn ghost" id="btnClear">清空</button>
</div>
<div id="toast"></div>
<footer>数据内置自 picosql-resource-pack 注册表 + hue_table_spider DDL + 历史会话口径沉淀（构建日期 __BUILD__）。口径修订：编辑 calibers.json 后重跑 build_page.py。</footer>
<script>
const DATA = __DATA__;
const LS_KEY = 'sqltable.copier.sel.v1';

// ---- 渲染 ----
const nav = document.getElementById('nav'), main = document.getElementById('main');
DATA.groups.forEach((g, gi) => {
  const a = document.createElement('a');
  a.href = '#g' + gi;
  a.innerHTML = '<span>' + g.name + '</span><span class="cnt">' + g.tables.length + '</span>';
  nav.appendChild(a);

  const h = document.createElement('div');
  h.className = 'group-title'; h.id = 'g' + gi;
  h.innerHTML = '<span>' + g.name + ' <span style="color:var(--sub);font-weight:400;font-size:12px">' + g.tables.length + ' 张</span></span>';
  const selAll = document.createElement('span');
  selAll.className = 'btn-sel'; selAll.textContent = '全选';
  selAll.onclick = () => {
    const on = ![...document.querySelectorAll('[data-group="' + gi + '"]:not(.hide)')].every(c => c.classList.contains('sel'));
    document.querySelectorAll('[data-group="' + gi + '"]:not(.hide)').forEach(c => {
      const cb = c.querySelector('input[type=checkbox]');
      cb.checked = on; toggleCard(c, on, true);
    });
    saveSel();
  };
  h.appendChild(selAll);
  main.appendChild(h);

  const wrap = document.createElement('div');
  wrap.className = 'cards';
  g.tables.forEach(t => wrap.appendChild(card(t, gi)));
  main.appendChild(wrap);
});
const emptyTip = document.createElement('div');
emptyTip.className = 'empty'; emptyTip.style.display = 'none';
emptyTip.textContent = '没有匹配的表，换个关键词试试';
main.appendChild(emptyTip);

function card(t, gi) {
  const el = document.createElement('div');
  el.className = 'card'; el.dataset.group = gi; el.dataset.key = t.n;
  el.dataset.search = (t.n + ' ' + t.u + ' ' + t.g + ' ' + t.j + ' ' + t.notes.join(' ') + ' ' + t.cols.map(c => c.n + c.c).join(' ')).toLowerCase();
  const badge = t.ddl_missing ? '<span class="badge miss">DDL缺</span> ' : '';
  const meta = [];
  if (t.g) meta.push('<b>粒度</b> ' + t.g);
  if (t.b) meta.push('<b>日期</b> ' + t.b);
  if (t.j) meta.push('<b>join</b> ' + t.j);
  el.innerHTML =
    '<div class="row1"><input type="checkbox"><div style="flex:1;min-width:0">' +
    '<div class="tname">' + t.n + '</div>' +
    '<div style="margin-top:2px"><span class="badge ' + t.d + '">' + (t.d === 'mysql' ? 'MySQL' : 'Impala') + '</span> ' + badge + '</div>' +
    '<div class="usage">' + (t.u || '') + '</div>' +
    (meta.length ? '<div class="meta">' + meta.join('<br>') + '</div>' : '') +
    '</div></div>' +
    (t.cols.length ? '<details><summary>关键列 ' + t.cols.length + (t.n_all > t.cols.length ? ' / 共' + t.n_all : '') + '</summary><table class="cols">' +
      t.cols.map(c => '<tr><td class="n">' + c.n + '</td><td class="t">' + c.t + '</td><td>' + (c.c || '') + '</td></tr>').join('') +
      '</table></details>' : '') +
    (t.notes.length ? '<details open><summary>口径与坑 ' + t.notes.length + ' 条</summary><ul class="notes">' +
      t.notes.map(n => '<li>' + n + '</li>').join('') + '</ul></details>' : '');
  const cb = el.querySelector('input[type=checkbox]');
  cb.addEventListener('change', () => { toggleCard(el, cb.checked); saveSel(); });
  return el;
}

// ---- 勾选状态 ----
function toggleCard(el, on, skipSave) {
  el.classList.toggle('sel', on);
  const cb = el.querySelector('input[type=checkbox]');
  if (cb.checked !== on) cb.checked = on;
  updateCnt();
}
function updateCnt() {
  const n = document.querySelectorAll('.card.sel').length;
  document.getElementById('selcnt').textContent = n;
}
function saveSel() {
  const keys = [...document.querySelectorAll('.card.sel')].map(c => c.dataset.key);
  localStorage.setItem(LS_KEY, JSON.stringify(keys));
}
(function restore() {
  let keys = [];
  try { keys = JSON.parse(localStorage.getItem(LS_KEY) || '[]'); } catch (e) {}
  keys.forEach(k => {
    const el = document.querySelector('[data-key="' + k + '"]');
    if (el) { el.classList.add('sel'); el.querySelector('input[type=checkbox]').checked = true; }
  });
  updateCnt();
})();

// ---- 搜索 ----
document.getElementById('search').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  let any = false;
  document.querySelectorAll('.card').forEach(c => {
    const hit = !q || c.dataset.search.includes(q);
    c.classList.toggle('hide', !hit);
    if (hit) any = true;
  });
  document.querySelectorAll('.group-title').forEach(h => {
    const gi = h.id.slice(1);
    const vis = [...document.querySelectorAll('[data-group="' + gi + '"]:not(.hide)')].length > 0;
    h.style.display = (!q || vis) ? '' : 'none';
  });
  emptyTip.style.display = any ? 'none' : (q ? '' : 'none');
});

// ---- 复制 ----
function selectedTables() {
  const keys = [...document.querySelectorAll('.card.sel')].map(c => c.dataset.key);
  const out = [];
  DATA.groups.forEach(g => g.tables.forEach(t => { if (keys.includes(t.n)) out.push({ g: g.name, t: t }); }));
  return out;
}
function buildFull() {
  const sel = selectedTables();
  if (!sel.length) return '';
  const L = [];
  L.push('【需求】（在这里描述业务问题）');
  L.push('【时间范围】（如：近30日，按日粒度，截止昨日）');
  L.push('');
  L.push('【可用表】（口径已核实，直接采用；不要另猜表）');
  sel.forEach((x, i) => {
    const t = x.t;
    L.push((i + 1) + '. ' + t.n + ' — ' + (t.u || '') + '（' + (t.d === 'mysql' ? 'MySQL' : 'Impala') + '）');
    const meta = [];
    if (t.g) meta.push('粒度: ' + t.g);
    if (t.b) meta.push('业务日期: ' + t.b);
    if (t.j) meta.push('join键: ' + t.j);
    if (meta.length) L.push('   ' + meta.join(' | '));
    if (t.cols.length) L.push('   关键列: ' + t.cols.map(c => c.n + (c.c ? '(' + c.c + ')' : '')).join(', '));
    t.notes.forEach(n => L.push('   ⚠ ' + n));
    if (t.ddl_missing) L.push('   ⚠ 本表 DDL 未入本地注册表，使用前先 DESCRIBE 确认列');
    L.push('');
  });
  L.push('【全局规则】');
  DATA.rules.forEach(r => L.push('- ' + r));
  L.push('');
  L.push('请基于以上表与口径写 SQL；先给口径确认点（若有歧义），再给完整 SQL。');
  return L.join('\n');
}
function buildNames() {
  const sel = selectedTables();
  return sel.map(x => x.t.n).join('\n');
}
function copyText(txt, label) {
  const done = () => {
    const t = document.getElementById('toast');
    t.textContent = '✅ 已复制：' + label;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 1800);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(done).catch(() => fallbackCopy(txt, done));
  } else fallbackCopy(txt, done);
}
function fallbackCopy(txt, done) {
  const ta = document.createElement('textarea');
  ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta); done();
}
document.getElementById('btnCopyFull').onclick = () => {
  const txt = buildFull();
  if (!txt) { toast('先勾选至少一张表'); return; }
  copyText(txt, '完整口径卡片（' + selectedTables().length + ' 张表 + 全局规则）');
};
document.getElementById('btnCopyNames').onclick = () => {
  const txt = buildNames();
  if (!txt) { toast('先勾选至少一张表'); return; }
  copyText(txt, '表名清单（' + selectedTables().length + ' 张）');
};
document.getElementById('btnClear').onclick = () => {
  document.querySelectorAll('.card.sel').forEach(c => { c.classList.remove('sel'); c.querySelector('input[type=checkbox]').checked = false; });
  saveSel(); updateCnt();
};
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
