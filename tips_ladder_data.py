#!/usr/bin/env python3
"""Refresh the data behind the TIPS Ladder Builder.

Pulls the outstanding TIPS and their index ratios from Treasury FiscalData, the Treasury par real yield curve
and the Federal Reserve fitted TIPS curve; computes index ratios for the next settlement date; applies
the rung selection rule; writes tips_ladder_data.json and injects it into tips_ladder.html.

Also downloads the holdings of the iShares iBonds Term TIPS ETFs (bullet funds) for the page's ETF option.

Usage:  python3 tips_ladder_data.py            (writes files next to this script)
        python3 tips_ladder_data.py --settle 2026-10-01

Selection rule (decided Sept 29, 2026): each income year is funded by the TIPS maturing between the
previous October and that January with the lowest index ratio, which is the 5-year issue where one
exists; otherwise the earliest maturity in the year. Bonds maturing within 60 days of settlement are skipped. 2037-2039 have no maturities and are funded in the
page with extra January 2036 TIPS.
"""
import csv, io, json, re, sys, urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")

def next_business_day(d):
    d += timedelta(days=1)
    while d.weekday() >= 5: d += timedelta(days=1)
    return d

# iShares iBonds Term TIPS ETFs (bullet funds). Each holds TIPS maturing Jan 1 to Oct 15 of its year, then pays
# out and closes; under the October rule a fund maturing in year Y funds income year Y+1. Product pages are found
# on the iShares site; this list is the fallback if discovery fails. Expense ratio 0.10% for every fund in the series.
ISHARES = "https://www.ishares.com"
IBONDS_TIPS = {  # year: (ticker, product path)
    2027: ("IBID", "/us/products/333135/ishares-ibonds-oct-2027-term-tips-etf"),
    2028: ("IBIE", "/us/products/333132/ishares-ibonds-oct-2028-term-tips-etf"),
    2029: ("IBIF", "/us/products/333141/ishares-ibonds-oct-2029-term-tips-etf"),
    2030: ("IBIG", "/us/products/333128/ishares-ibonds-oct-2030-term-tips-etf"),
    2031: ("IBIH", "/us/products/333121/ishares-ibonds-oct-2031-term-tips-etf"),
    2032: ("IBII", "/us/products/333079/ishares-ibonds-oct-2032-term-tips-etf"),
    2033: ("IBIJ", "/us/products/333076/ishares-ibonds-oct-2033-term-tips-etf"),
    2034: ("IBIK", "/us/products/337462/ishares-ibonds-oct-2034-term-tips-etf"),
    2035: ("IBIL", "/us/products/342148/ishares-ibonds-oct-2035-term-tips-etf"),
    2036: ("IBIM", "/us/products/350034/ishares-ibonds-oct-2036-term-tips-etf"),
}
ETF_FEE = 0.10

def fetch_etfs(settle, known):
    """Holdings of each iBonds Term TIPS ETF that has not yet matured. Returns a list (may be empty)."""
    funds = dict(IBONDS_TIPS)
    try:  # discover new funds (for example a new October year) from the product list
        page = get(ISHARES + "/us/products/etf-investments")
        for m in set(re.findall(r"(/us/products/\d+/ishares-ibonds-oct-(\d{4})-term-tips-etf)", page)):
            y = int(m[1])
            if y not in funds: funds[y] = (None, m[0])
    except Exception:
        pass
    out = []
    for y in sorted(funds):
        if date(y, 10, 15) <= settle + timedelta(days=60): continue  # matured or about to
        ticker, path = funds[y]
        try:
            if not ticker:
                t = re.search(r"<title>[^<|]*\|\s*([A-Z]{3,5})\b", get(ISHARES + path))
                ticker = t.group(1) if t else f"Oct {y}"
            lines = get(ISHARES + path + "/latest-holdings.csv").lstrip("\ufeff").split("\n")
            asof = next((l.split(",", 1)[1].strip().strip('"') for l in lines[:5] if l.startswith("Fund Holdings as of")), "")
            start = next(i for i, l in enumerate(lines) if l.startswith("Name,"))
            hold = []
            for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
                if r.get("Asset Class") != "Fixed Income" or r.get("CUSIP") not in known: continue
                par = float(r["Par Value"].replace(",", ""))
                hold.append(dict(cusip=r["CUSIP"], par=par, weight=float(r["Weight (%)"]),
                                 realYtm=float(r["Real YTM (%)"]) if r.get("Real YTM (%)") not in (None, "", "-") else None))
            if not hold: continue
            tp = sum(h["par"] for h in hold)
            for h in hold: h["parShare"] = round(h["par"] / tp, 6)
            out.append(dict(ticker=ticker, name=f"iShares iBonds Oct {y} Term TIPS ETF", maturity=f"{y}-10-15",
                            fundsYear=y + 1, fee=ETF_FEE, holdingsAsOf=asof, url=ISHARES + path,
                            holdings=[dict(cusip=h["cusip"], parShare=h["parShare"], weight=h["weight"], realYtm=h["realYtm"]) for h in hold]))
        except Exception as e:
            print(f"  ETF {y}: holdings unavailable ({e})")
    return out

def main():
    settle = None
    for a in sys.argv:
        if a.startswith("--settle="): settle = date.fromisoformat(a.split("=")[1])
    settle = settle or next_business_day(date.today())

    # 1. Outstanding TIPS (original auctions only) maturing after settlement
    url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
           f"?filter=inflation_index_security:eq:Yes,reopening:eq:No,maturity_date:gt:{settle}"
           "&fields=cusip,original_security_term,int_rate,dated_date,maturity_date,ref_cpi_on_dated_date&page[size]=500")
    rows = json.loads(get(url))["data"]
    bonds = []
    for r in rows:
        # skip announced auctions that have not happened or settled yet (coupon or reference CPI still blank)
        if any(str(r.get(k)).strip().lower() in ("", "null", "none") for k in ("int_rate", "ref_cpi_on_dated_date", "dated_date")):
            continue
        if date.fromisoformat(r["dated_date"]) > settle:
            continue
        term = r["original_security_term"].split("-")[0] + "-year"
        m = date.fromisoformat(r["maturity_date"])
        bonds.append(dict(cusip=r["cusip"], term=term, coupon=float(r["int_rate"]), dated=r["dated_date"],
                          maturity=r["maturity_date"], refCpi=float(r["ref_cpi_on_dated_date"]),
                          name=f"{float(r['int_rate']):.3f}% TIPS of {m.strftime('%B %Y')}", issued=r["dated_date"][:4]))
    bonds = {b["cusip"]: b for b in bonds}.values()
    bonds = sorted(bonds, key=lambda b: b["maturity"])

    # 2. Index ratios for the settlement date, straight from Treasury's TIPS/CPI table (published about two weeks ahead)
    url = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/tips_cpi_data_detail"
           f"?filter=index_date:eq:{settle}&fields=cusip,index_ratio,ref_cpi&page[size]=500")
    ir = {r["cusip"]: (float(r["index_ratio"]), float(r["ref_cpi"])) for r in json.loads(get(url))["data"]}
    if not ir: sys.exit(f"Treasury has not published index ratios for {settle} yet; pass an earlier --settle.")
    ref = next(iter(ir.values()))[1]
    for b in bonds:
        if b["cusip"] in ir: b["indexRatio"] = round(ir[b["cusip"]][0], 5)
        else: b["indexRatio"] = round(ref / b["refCpi"], 5)

    # 3. Real yield curve: Treasury 5/7/10/20/30 (latest) and Fed fitted 2/3/4 (latest), Fed shifted to Treasury 5-year level
    tcsv = get(f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{settle.year}/all?type=daily_treasury_real_yield_curve&field_tdr_date_value={settle.year}&page&_format=csv")
    trows = list(csv.DictReader(io.StringIO(tcsv)))
    t = trows[0]  # newest first
    def tv(k):
        for kk in t:
            if kk.strip().upper().startswith(k): return float(t[kk])
    tcurve = {5: tv("5 YR"), 7: tv("7 YR"), 10: tv("10 YR"), 20: tv("20 YR"), 30: tv("30 YR")}
    tdate = t["Date"]
    fcurve, fdate, fed_vals = None, None, None
    for fu in ("https://www.federalreserve.gov/data/yield-curve-tables/feds200805.csv",
               "https://www.federalreserve.gov/data/yield-curve-models/feds200805.csv"):
        try:
            fed = get(fu)
            lines = fed.splitlines()
            header = [l for l in lines if l.startswith("Date,")][0].split(",")
            for l in reversed([l for l in lines if re.match(r"\d{4}-\d{2}-\d{2},", l)]):
                vals = dict(zip(header, l.split(",")))
                if vals.get("TIPSPY02") not in (None, "", "NA"):
                    fcurve = {k: float(vals[f"TIPSPY{k:02d}"]) for k in (2, 3, 4, 5)}; fdate = vals["Date"]; fed_vals = vals; break
            if fcurve: break
        except Exception:
            continue
    if not fcurve:  # fall back to the Fed's HTML table of recent values
        try:
            html = get("https://www.federalreserve.gov/data/yield-curve-tables/feds200805_1.html")
            hdr = re.findall(r"<th[^>]*>(.*?)</th>", html)
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S)
            for row in reversed(rows):
                cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
                if len(cells) == len(hdr) and re.match(r"\d{4}-\d{2}-\d{2}", cells[0]):
                    vals = dict(zip(hdr, cells))
                    if vals.get("TIPSPY02") not in ("", "NA"):
                        fcurve = {k: float(vals[f"TIPSPY{k:02d}"]) for k in (2, 3, 4, 5)}; fdate = cells[0]; fed_vals = vals; break
        except Exception:
            pass
    if not fcurve:  # last resort: flat at the 5-year
        fcurve = {2: tcurve[5], 3: tcurve[5], 4: tcurve[5], 5: tcurve[5]}; fdate = "unavailable; 2-4 yr set equal to the 5-year"
    # 2-4 years: straight-line interpolation between the 1-year point (below) and Treasury's 5-year.
    # (The Fed fitted curve is only used for its breakeven figure; checked Sept 29, 2026: the shifted Fed
    # shape overstated the 3-year by ~16 bp against broker quotes, interpolation was within 2 bp.)
    curve = dict(tcurve)

    # Short end (under 2 years): real yield = Treasury nominal par yield (6 mo, 1 yr) minus the Fed's 2-year
    # breakeven inflation. Checked Sept 29, 2026 against broker quotes: within a few bp.
    short_note = ""
    try:
        ncsv = get(f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{settle.year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={settle.year}&page&_format=csv")
        nrow = list(csv.DictReader(io.StringIO(ncsv)))[0]
        def nv(k):
            for kk in nrow:
                if kk.strip().upper().startswith(k): return float(nrow[kk])
        n6, n12 = nv("6 MO"), nv("1 YR")
        bk = float(fed_vals.get("BKEVEN02")) if fed_vals and fed_vals.get("BKEVEN02") not in (None, "", "NA") else None
        if bk is not None and n6 is not None and n12 is not None:
            curve = {0.5: round(n6 - bk, 2), 1: round(n12 - bk, 2), **curve}
            short_note = f" Under 5 yr: Treasury nominal 6-month and 1-year yields ({nrow['Date']}) less the Fed 2-year breakeven inflation rate ({bk:.2f}%), interpolated to the 5-year."
    except Exception:
        pass

    # 4. Selection rule
    byyear = {}
    years = sorted({int(b["maturity"][:4]) for b in bonds} | {int(b["maturity"][:4]) + 1 for b in bonds})
    for Y in years:
        if Y <= settle.year: continue  # income years start with the next calendar year
        cands = [b for b in bonds if (b["maturity"][:4] == str(Y - 1) and int(b["maturity"][5:7]) >= 10)
                 or (b["maturity"][:4] == str(Y) and int(b["maturity"][5:7]) <= 2)]
        cands = [b for b in cands if b["maturity"] > (settle + timedelta(days=60)).isoformat()]  # skip bonds within 60 days of maturity
        if cands:
            byyear[Y] = sorted(cands, key=lambda b: (b["indexRatio"], b["maturity"]))[0]
        else:
            same = sorted([b for b in bonds if b["maturity"][:4] == str(Y) and b["maturity"] > (settle + timedelta(days=60)).isoformat()], key=lambda b: (b["maturity"], b["indexRatio"]))
            if same: byyear[Y] = same[0]

    # 5. Bullet TIPS ETFs; keep the previous night's list if iShares cannot be reached
    known = {b["cusip"] for b in bonds}
    etfs = fetch_etfs(settle, known)
    if not etfs:
        try:
            prev = json.loads((HERE / "tips_ladder_data.json").read_text()).get("etfs", [])
            etfs = [e for e in prev if all(h["cusip"] in known for h in e["holdings"]) and e["maturity"] > (settle + timedelta(days=60)).isoformat()]
            if etfs: print("  ETF holdings: iShares unavailable, kept previous data")
        except Exception:
            etfs = []

    out = dict(dataAsOf=date.today().isoformat(), settlement=settle.isoformat(), refCpiSettlement=round(ref, 5),
               yieldsAsOf=tdate,
               yieldNote=f"Treasury par real yield curve (5-30 yr) as of {tdate}." + short_note,
               curve={str(k): v for k, v in curve.items()},
               selectionRule="Each income year is funded by the TIPS maturing between the previous October and that January with the lowest index ratio (the 5-year issue where one exists); otherwise the earliest maturity in the year. Bonds maturing within 60 days of settlement are skipped.",
               ladder=[dict(year=y, **byyear[y]) for y in sorted(byyear)], etfs=etfs, allBonds=list(bonds))
    (HERE / "tips_ladder_data.json").write_text(json.dumps(out, indent=1))

    slim = {k: out[k] for k in ["dataAsOf", "settlement", "refCpiSettlement", "yieldsAsOf", "yieldNote", "curve", "ladder"]}
    need = {h["cusip"] for e in etfs for h in e["holdings"]} - {b["cusip"] for b in out["ladder"]}
    slim["etfs"] = [{k: v for k, v in e.items() if k != "url"} for e in etfs]
    slim["etfBonds"] = [b for b in bonds if b["cusip"] in need]
    for name in ("tips_ladder.html", "tips_ladder_wordpress.html"):
        page = HERE / name
        if page.exists():
            s = page.read_text()
            s, n = re.subn(r"(const|let) DATA = \{.*?\};\n", lambda m: m.group(1) + " DATA = " + json.dumps(slim) + ";\n", s, count=1, flags=re.S)
            if n: page.write_text(s)
    print(f"Settlement {settle}, ref CPI {ref:.4f}, {len(list(bonds))} TIPS outstanding, curve {curve} (Treasury {tdate})")
    for y in sorted(byyear):
        b = byyear[y]; print(f"  {y}: {b['name']} ({b['term']}, {b['cusip']}) index ratio {b['indexRatio']}")
    for e in etfs:
        print(f"  ETF {e['ticker']} matures {e['maturity']}, funds {e['fundsYear']}: " + ", ".join(f"{h['cusip']} {h['parShare']:.3f}" for h in e["holdings"]))

if __name__ == "__main__":
    main()
