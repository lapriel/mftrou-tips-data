#!/usr/bin/env python3
"""Refresh the data behind the TIPS Ladder Builder.

Pulls the outstanding TIPS and their index ratios from Treasury FiscalData, the Treasury par real yield curve
and the Federal Reserve fitted TIPS curve; computes index ratios for the next settlement date; applies
the rung selection rule; writes tips_ladder_data.json and injects it into tips_ladder.html.

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
    shift = tcurve[5] - fcurve[5]
    curve = {2: round(fcurve[2] + shift, 2), 3: round(fcurve[3] + shift, 2), 4: round(fcurve[4] + shift, 2), **tcurve}

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
            short_note = f" Under 2 yr: Treasury nominal 6-month and 1-year yields ({nrow['Date']}) less the Fed 2-year breakeven inflation rate ({bk:.2f}%)."
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

    out = dict(dataAsOf=date.today().isoformat(), settlement=settle.isoformat(), refCpiSettlement=round(ref, 5),
               yieldsAsOf=tdate,
               yieldNote=f"Treasury par real yield curve (5-30 yr) as of {tdate}; 2-4 yr from the Federal Reserve Board fitted TIPS curve ({fdate}), shifted to the Treasury 5-year level." + short_note,
               curve={str(k): v for k, v in curve.items()},
               selectionRule="Each income year is funded by the TIPS maturing between the previous October and that January with the lowest index ratio (the 5-year issue where one exists); otherwise the earliest maturity in the year. Bonds maturing within 60 days of settlement are skipped.",
               ladder=[dict(year=y, **byyear[y]) for y in sorted(byyear)], allBonds=list(bonds))
    (HERE / "tips_ladder_data.json").write_text(json.dumps(out, indent=1))

    slim = {k: out[k] for k in ["dataAsOf", "settlement", "refCpiSettlement", "yieldsAsOf", "yieldNote", "curve", "ladder"]}
    for name in ("tips_ladder.html", "tips_ladder_wordpress.html"):
        page = HERE / name
        if page.exists():
            s = page.read_text()
            s, n = re.subn(r"(const|let) DATA = \{.*?\};\n", lambda m: m.group(1) + " DATA = " + json.dumps(slim) + ";\n", s, count=1, flags=re.S)
            if n: page.write_text(s)
    print(f"Settlement {settle}, ref CPI {ref:.4f}, {len(list(bonds))} TIPS outstanding, curve {curve} (Treasury {tdate})")
    for y in sorted(byyear):
        b = byyear[y]; print(f"  {y}: {b['name']} ({b['term']}, {b['cusip']}) index ratio {b['indexRatio']}")

if __name__ == "__main__":
    main()
