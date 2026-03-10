"""
ETF Flows Updater
-----------------
1. Scrapa farside.co.uk e atualiza os CSVs.
2. Gera um arquivo HTML interativo (docs/index.html) com:
   - Gráfico de barras diário
   - Gráfico de linha acumulativo (sem bolinhas)
   - Seletor de período: 1W · 1M · 3M · 1Y · YTD · MAX
   - Toggle dark/light mode

Uso local:
    python etf_flows_update.py

Dependências:
    pip install -r requirements.txt
"""

import os
import cloudscraper
from bs4 import BeautifulSoup
import csv
import json
import re
from datetime import datetime
from pathlib import Path

# ──────────────────────────────────────────────────────────────
# Caminhos configuráveis via variáveis de ambiente.
# Localmente usa os defaults abaixo; no GitHub Actions o
# workflow define BTC_CSV, ETH_CSV e CHART_HTML via env:.
# ──────────────────────────────────────────────────────────────
_DEFAULT_DATA = Path(__file__).parent / "data"
_DEFAULT_DOCS = Path(__file__).parent / "docs"

BTC_CSV    = Path(os.getenv("BTC_CSV",    str(_DEFAULT_DATA / "ETF flow - BTC.csv")))
ETH_CSV    = Path(os.getenv("ETH_CSV",    str(_DEFAULT_DATA / "ETF flow - ETH.csv")))
CHART_HTML = Path(os.getenv("CHART_HTML", str(_DEFAULT_DOCS / "index.html")))

# Modo CI: quando BTC_CSV vem de env var, não há vault do Obsidian
_CI = "BTC_CSV" in os.environ

SOURCES = {
    "BTC": ("https://farside.co.uk/btc/", BTC_CSV),
    "ETH": ("https://farside.co.uk/eth/", ETH_CSV),
}

SUMMARY_LABELS = {"Total", "Average", "Maximum", "Minimum", "Fee"}
# ──────────────────────────────────────────────────────────────

_scraper = cloudscraper.create_scraper()


# ── Scraping & CSV update ──────────────────────────────────────

def normalize_date(date_str: str) -> str:
    s = date_str.strip().rstrip(".")
    s = " ".join(p.rstrip(".") for p in s.split())
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    return date_str.strip()


def fetch_table(url: str) -> list:
    resp = _scraper.get(url, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    tables = soup.find_all("table")
    if len(tables) < 2:
        raise ValueError(f"Tabela de dados não encontrada em {url}")
    rows = []
    for tr in tables[1].find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    return rows


def normalize_value(value: str) -> str:
    s = value.strip()
    if s in ("-", "–", "—", ""):
        return "0"
    if s.lower() in ("n/a", "na", "n.a."):
        return ""
    return s


def parse_table(raw_rows: list) -> tuple:
    if not raw_rows or len(raw_rows) < 2:
        return [], []
    ticker_row = raw_rows[1]
    header = ["Date"] + [c for c in ticker_row if c]
    if raw_rows[0] and raw_rows[0][-1] == "Total" and "Total" not in header:
        header.append("Total")
    data = []
    for row in raw_rows[4:]:
        if not row or not row[0].strip():
            continue
        if row[0].strip() in SUMMARY_LABELS:
            continue
        norm_date = normalize_date(row[0])
        values = [normalize_value(c) for c in row[1:]]
        if all(v in ("0", "0.0", "") for v in values if v != ""):
            continue
        full_row = [norm_date] + values
        padded = full_row + [""] * (len(header) - len(full_row))
        data.append(padded[:len(header)])
    return header, data


def load_csv_dates(csv_path: Path) -> set:
    if not csv_path.exists():
        return set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        return {normalize_date(row[0]) for row in reader if row and row[0].strip()}


def update_csv(csv_path: Path, header: list, data: list) -> int:
    existing_dates = load_csv_dates(csv_path)
    new_rows = [row for row in data if normalize_date(row[0]) not in existing_dates]
    if not new_rows:
        return 0
    file_exists = csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if file_exists:
        with open(csv_path, "rb+") as f:
            f.seek(0, 2)
            if f.tell() > 0:
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    f.write(b"\n")
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)
        writer.writerows(new_rows)
    return len(new_rows)


# ── Chart data ─────────────────────────────────────────────────

def _parse_num(s: str) -> float:
    s = s.strip()
    if not s or s in ("-", "–", "—"):
        return 0.0
    if s.startswith("(") and s.endswith(")"):
        try:
            return -float(s[1:-1].replace(",", ""))
        except ValueError:
            return 0.0
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0


def _parse_dt(s: str):
    s = s.strip().rstrip(".")
    s = " ".join(p.rstrip(".") for p in s.split())
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def compute_chart_data():
    btc_map = {}
    with open(BTC_CSV, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if not row or not row[0].strip():
                continue
            d = _parse_dt(row[0])
            if d:
                btc_map[d] = _parse_num(row[-1])

    eth_map = {}
    with open(ETH_CSV, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for _ in range(4):
            next(reader, None)
        for row in reader:
            if not row or not row[0].strip():
                continue
            d = _parse_dt(row[0])
            if not d:
                continue
            vals = [v for v in row[1:] if v.strip()]
            if vals:
                eth_map[d] = _parse_num(vals[-1])

    all_dates = sorted(set(btc_map) | set(eth_map))
    btc_vals  = [btc_map.get(d, 0.0) for d in all_dates]
    eth_vals  = [eth_map.get(d, 0.0) for d in all_dates]

    btc_cum, eth_cum = [], []
    sb = se = 0.0
    for b, e in zip(btc_vals, eth_vals):
        sb += b; btc_cum.append(round(sb, 1))
        se += e; eth_cum.append(round(se, 1))

    return all_dates, btc_vals, eth_vals, btc_cum, eth_cum


# ── HTML generation ────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="pt-BR" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETF Flows — BTC &amp; ETH</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
  [data-theme="dark"] {
    --bg:#1a1a1a; --surface:#222; --border:#333;
    --text:#ccc; --muted:#777; --accent:#87baff;
    --grid:rgba(255,255,255,.04); --grid-y:rgba(255,255,255,.07);
    --tick:#555; --tt-bg:#2a2a2a; --tt-border:#444; --tt-text:#bbb; --legend:#999;
  }
  [data-theme="light"] {
    --bg:#f5f5f5; --surface:#fff; --border:#ddd;
    --text:#222; --muted:#888; --accent:#1a5ab4;
    --grid:rgba(0,0,0,.04); --grid-y:rgba(0,0,0,.07);
    --tick:#aaa; --tt-bg:#fff; --tt-border:#ccc; --tt-text:#333; --legend:#555;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body {
    font-family:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif;
    background:var(--bg); color:var(--text);
    padding:14px 18px 20px; transition:background .2s,color .2s;
  }
  .header { display:flex; align-items:center; justify-content:space-between; margin-bottom:14px; gap:10px; flex-wrap:wrap; }
  .header-left { display:flex; align-items:center; gap:12px; }
  .title  { font-size:13px; font-weight:600; letter-spacing:.02em; }
  .updated{ font-size:11px; color:var(--muted); }
  .theme-btn {
    padding:3px 9px; border:1px solid var(--border);
    background:transparent; color:var(--muted);
    border-radius:4px; cursor:pointer; font-size:13px; line-height:1.6;
    transition:border-color .15s;
  }
  .theme-btn:hover { border-color:var(--accent); }
  .range-bar { display:flex; gap:5px; margin-bottom:18px; flex-wrap:wrap; }
  .rbtn {
    padding:3px 11px; border:1px solid var(--border);
    background:transparent; color:var(--muted);
    border-radius:4px; cursor:pointer; font-size:11px; font-weight:600;
    letter-spacing:.04em; transition:all .15s;
  }
  .rbtn:hover { color:var(--text); border-color:var(--muted); }
  .rbtn.on { background:rgba(135,186,255,.15); border-color:var(--accent); color:var(--accent); }
  [data-theme="light"] .rbtn.on { background:rgba(26,90,180,.1); }
  .section { margin-bottom:22px; }
  .clabel  { font-size:10px; font-weight:600; letter-spacing:.07em; text-transform:uppercase; color:var(--muted); margin-bottom:7px; }
  .cbox {
    position:relative; height:260px;
    background:var(--surface); border:1px solid var(--border);
    border-radius:8px; padding:10px 14px 8px;
    transition:background .2s,border-color .2s;
  }
  .cbox canvas { position:absolute; inset:10px 14px 8px; width:calc(100% - 28px) !important; height:calc(100% - 18px) !important; }
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <span class="title">ETF Flows &mdash; BTC &amp; ETH</span>
    <span class="updated" id="upd"></span>
  </div>
  <button class="theme-btn" id="themeBtn" onclick="toggleTheme()" title="Alternar tema">☀️</button>
</div>
<div class="range-bar">
  <button class="rbtn" data-r="1W"  onclick="sr(this)">1W</button>
  <button class="rbtn" data-r="1M"  onclick="sr(this)">1M</button>
  <button class="rbtn" data-r="3M"  onclick="sr(this)">3M</button>
  <button class="rbtn" data-r="1Y"  onclick="sr(this)">1Y</button>
  <button class="rbtn" data-r="YTD" onclick="sr(this)">YTD</button>
  <button class="rbtn on" data-r="MAX" onclick="sr(this)">MAX</button>
</div>
<div class="section">
  <div class="clabel">Fluxo Diário &mdash; US$ M</div>
  <div class="cbox"><canvas id="bar"></canvas></div>
</div>
<div class="section">
  <div class="clabel">Fluxo Acumulativo &mdash; US$ M</div>
  <div class="cbox"><canvas id="line"></canvas></div>
</div>
<script>
const ISO=__DATES_ISO__;const LABELS=__ALL_LABELS__;
const BD=__BTC_DAILY__;const ED=__ETH_DAILY__;
const BC=__BTC_CUM__;const EC=__ETH_CUM__;
document.getElementById('upd').textContent='Atualizado em '+new Date(ISO[ISO.length-1]).toLocaleDateString('pt-BR',{day:'2-digit',month:'short',year:'numeric'});
function sliceIdx(r){const last=new Date(ISO[ISO.length-1]);if(r==='MAX')return 0;let s;if(r==='YTD'){s=new Date(last.getFullYear(),0,1);}else{const d={'1W':7,'1M':30,'3M':90,'1Y':365}[r];s=new Date(last);s.setDate(last.getDate()-d);}const i=ISO.findIndex(d=>new Date(d)>=s);return i===-1?0:i;}
const xC={ticks:{color:'#555',font:{size:10},maxTicksLimit:9,maxRotation:0},grid:{color:'rgba(255,255,255,.04)'}};
const yC={ticks:{color:'#555',font:{size:10},callback:v=>Math.abs(v)>=1000?(v/1000).toFixed(0)+'k':v},grid:{color:'rgba(255,255,255,.07)'}};
const ttC={backgroundColor:'#2a2a2a',titleColor:'#bbb',bodyColor:'#bbb',borderColor:'#444',borderWidth:1,padding:9,callbacks:{label:c=>'  '+c.dataset.label+':  '+c.parsed.y.toFixed(1)+' M'}};
const lgC={labels:{color:'#999',font:{size:11},boxWidth:11,padding:14}};
const base={responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:lgC,tooltip:ttC},scales:{x:xC,y:yC},animation:false};
const barChart=new Chart(document.getElementById('bar'),{type:'bar',data:{labels:LABELS,datasets:[{label:'BTC',data:BD,backgroundColor:'rgba(25,90,180,0.75)',borderColor:'rgba(25,90,180,1)',borderWidth:0,borderRadius:1,barPercentage:.85,categoryPercentage:.75},{label:'ETH',data:ED,backgroundColor:'rgba(84,156,255,0.75)',borderColor:'rgba(84,156,255,1)',borderWidth:0,borderRadius:1,barPercentage:.85,categoryPercentage:.75}]},options:base});
const lineChart=new Chart(document.getElementById('line'),{type:'line',data:{labels:LABELS,datasets:[{label:'BTC',data:BC,borderColor:'rgba(135,186,255,1)',backgroundColor:'rgba(135,186,255,0.25)',borderWidth:1.5,pointRadius:0,pointHoverRadius:4,tension:.35,fill:true},{label:'ETH',data:EC,borderColor:'rgba(25,90,180,1)',backgroundColor:'rgba(25,90,180,0.9)',borderWidth:1.5,pointRadius:0,pointHoverRadius:4,tension:.35,fill:true}]},options:base});
function sr(btn){document.querySelectorAll('.rbtn').forEach(b=>b.classList.remove('on'));btn.classList.add('on');const i=sliceIdx(btn.dataset.r);const sl=LABELS.slice(i);barChart.data.labels=sl;barChart.data.datasets[0].data=BD.slice(i);barChart.data.datasets[1].data=ED.slice(i);barChart.update('none');const ob=i>0?BC[i-1]:0;const oe=i>0?EC[i-1]:0;lineChart.data.labels=sl;lineChart.data.datasets[0].data=BC.slice(i).map(v=>+(v-ob).toFixed(1));lineChart.data.datasets[1].data=EC.slice(i).map(v=>+(v-oe).toFixed(1));lineChart.update('none');}
function applyTheme(t){document.documentElement.setAttribute('data-theme',t);document.getElementById('themeBtn').textContent=t==='dark'?'☀️':'🌙';localStorage.setItem('etf-theme',t);const dk=t==='dark';const tk=dk?'#555':'#aaa';const gx=dk?'rgba(255,255,255,.04)':'rgba(0,0,0,.04)';const gy=dk?'rgba(255,255,255,.07)':'rgba(0,0,0,.07)';const tb=dk?'#2a2a2a':'#fff';const tbd=dk?'#444':'#ccc';const tt=dk?'#bbb':'#333';const lg=dk?'#999':'#555';[barChart,lineChart].forEach(ch=>{ch.options.scales.x.ticks.color=tk;ch.options.scales.x.grid.color=gx;ch.options.scales.y.ticks.color=tk;ch.options.scales.y.grid.color=gy;ch.options.plugins.tooltip.backgroundColor=tb;ch.options.plugins.tooltip.borderColor=tbd;ch.options.plugins.tooltip.titleColor=tt;ch.options.plugins.tooltip.bodyColor=tt;ch.options.plugins.legend.labels.color=lg;ch.update('none');});}
function toggleTheme(){const c=document.documentElement.getAttribute('data-theme');applyTheme(c==='dark'?'light':'dark');}
applyTheme(localStorage.getItem('etf-theme')||'dark');
</script>
</body>
</html>
"""


def generate_chart_html(all_dates, btc_vals, eth_vals, btc_cum, eth_cum) -> str:
    html = _HTML_TEMPLATE
    html = html.replace("__DATES_ISO__",  json.dumps([d.strftime("%Y-%m-%d") for d in all_dates]))
    html = html.replace("__ALL_LABELS__", json.dumps([d.strftime("%-d %b '%y") for d in all_dates]))
    html = html.replace("__BTC_DAILY__",  json.dumps([round(v, 1) for v in btc_vals]))
    html = html.replace("__ETH_DAILY__",  json.dumps([round(v, 1) for v in eth_vals]))
    html = html.replace("__BTC_CUM__",    json.dumps(btc_cum))
    html = html.replace("__ETH_CUM__",    json.dumps(eth_cum))
    return html


# ── Main ───────────────────────────────────────────────────────

def main():
    print(f"\n{'='*55}")
    print(f"  ETF Flows Updater  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}\n")

    # 1. Atualiza CSVs
    for asset, (url, csv_path) in SOURCES.items():
        print(f"[{asset}] Buscando dados em {url} ...")
        try:
            raw_rows = fetch_table(url)
            header, data = parse_table(raw_rows)
            print(f"[{asset}] {len(data)} dias encontrados no site.")
            added = update_csv(csv_path, header, data)
            if added:
                print(f"[{asset}] ✅ {added} novas linhas adicionadas → {csv_path.name}")
            else:
                print(f"[{asset}] ✅ CSV já está atualizado. Nenhuma linha nova.")
        except Exception as e:
            print(f"[{asset}] ❌ Erro: {type(e).__name__}: {e}")

    # 2. Gera HTML interativo
    print("\n[CHART] Gerando HTML interativo ...")
    try:
        all_dates, btc_vals, eth_vals, btc_cum, eth_cum = compute_chart_data()
        html = generate_chart_html(all_dates, btc_vals, eth_vals, btc_cum, eth_cum)
        CHART_HTML.parent.mkdir(parents=True, exist_ok=True)
        CHART_HTML.write_text(html, encoding="utf-8")
        print(f"[CHART] ✅ {CHART_HTML.name} gerado com {len(all_dates)} dias de dados.")
    except Exception as e:
        print(f"[CHART] ❌ Erro ao gerar HTML: {type(e).__name__}: {e}")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    main()
