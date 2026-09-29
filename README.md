# mftrou-tips-data

Public data behind the Money for the Rest of Us TIPS Ladder Builder. Refreshed every weekday evening by GitHub Actions.

`tips_ladder_data.json` holds the outstanding TIPS (from Treasury FiscalData) with official index ratios for the next settlement date, the real yield curve (Treasury par real yields for 5-30 years; Federal Reserve fitted TIPS curve for 2-4 years; Treasury nominal 6-month and 1-year yields less the Fed 2-year breakeven for the short end), and the one TIPS selected for each income year.

Selection rule: each income year is funded by the TIPS maturing between the previous October and that January with the lowest index ratio (the 5-year issue where one exists); otherwise the earliest maturity in the year; bonds maturing within 60 days of settlement are skipped.

All source data is published by the U.S. Treasury and the Federal Reserve Board. This file is for education only and is not investment advice.

Page URL for the data: https://raw.githubusercontent.com/lapriel/mftrou-tips-data/main/tips_ladder_data.json
